"""Native admission broker plus a durable conservative token ledger per mission."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

from nooa.unifiedllm import (
    AdmissionBroker,
    AdmissionControl,
    AdmissionControlConfig,
    BrokerAdmissionConfig,
)


class BudgetExceeded(RuntimeError):
    pass


_attempts = ContextVar("research_provider_attempts", default=None)


class CountedController:
    """Observe actual native admission permits, including retries; never add a limiter."""

    def __init__(self, controller):
        self.controller = controller

    async def acquire(self, observer):
        permit = await self.controller.acquire(observer)
        counter = _attempts.get()
        if counter is not None:
            counter[0] += 1
        return permit

    def close(self):
        close = getattr(self.controller, "close", None)
        if close:
            close()


class TokenLedger:
    def __init__(self, root: Path, mission: str, limit: int):
        self.root = root
        self.mission = mission
        self.limit = limit
        self.path = root / "budget.sqlite"
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS tokens(
             id TEXT PRIMARY KEY,mission TEXT,agent TEXT,purpose TEXT,reserved INTEGER,charged INTEGER DEFAULT 0,
             state TEXT,usage TEXT,created REAL);""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, amount, agent, purpose):
        key = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN reserved ELSE charged END),0) FROM tokens WHERE mission=?",
                (self.mission,),
            ).fetchone()[0]
            if used + amount > self.limit:
                raise BudgetExceeded(
                    "Shared token budget exhausted (including in-flight reservations)"
                )
            db.execute(
                "INSERT INTO tokens(id,mission,agent,purpose,reserved,state,created) VALUES(?,?,?,?,?,?,?)",
                (key, self.mission, agent, purpose, amount, "reserved", time.time()),
            )
        return key

    def settle(self, key, usage=None, dispatched=True, unreported_retry_tokens=0):
        with self.connect() as db:
            row = db.execute("SELECT reserved FROM tokens WHERE id=?", (key,)).fetchone()
            # Unknown/failed usage consumes the reservation rather than silently becoming zero.
            charged = (
                (usage.total_tokens or usage.input_tokens + usage.output_tokens)
                if usage is not None
                else row[0]
                if dispatched
                else 0
            )
            db.execute(
                "UPDATE tokens SET state=?,charged=?,usage=? WHERE id=?",
                (
                    "reported_plus_estimate"
                    if usage is not None and unreported_retry_tokens
                    else "reported"
                    if usage is not None
                    else "estimated"
                    if dispatched
                    else "not_dispatched",
                    charged + unreported_retry_tokens,
                    json.dumps(
                        {
                            "reported": usage.model_dump() if usage is not None else None,
                            "unreported_retry_tokens": unreported_retry_tokens,
                        }
                    ),
                    key,
                ),
            )

    def abandon_agent(self, agent):
        with self.connect() as db:
            db.execute(
                "UPDATE tokens SET state='estimated',charged=reserved WHERE mission=? AND agent=? AND state='reserved'",
                (self.mission, agent),
            )

    def snapshot(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT state,COALESCE(SUM(reserved),0),COALESCE(SUM(charged),0),COUNT(*) FROM tokens WHERE mission=? GROUP BY state",
                (self.mission,),
            ).fetchall()
        charged = sum(r[2] for r in rows)
        held = sum(r[1] for r in rows if r[0] == "reserved")
        return {
            "token_limit": self.limit,
            "charged_tokens": charged,
            "reserved_tokens": held,
            "available_tokens": max(0, self.limit - charged - held),
            "logical_calls": sum(r[3] for r in rows),
            "estimated_calls": sum(
                r[3] for r in rows if r[0] in {"estimated", "reported_plus_estimate"}
            ),
        }


class BudgetedClient(AdmissionControl):
    def __init__(self, base, config, ledger, events, reserve_tokens, output_ceiling=50000):
        super().__init__(base, config)
        self.controller = CountedController(self.controller)
        self.ledger = ledger
        self.events = events
        self.reserve_tokens = reserve_tokens
        self.output_ceiling = output_ceiling
        self.budget_exhausted = False

    def call(self, *args, **kwargs):
        raise RuntimeError("Synchronous model calls are disabled; use budgeted async calls")

    async def acall(self, *args, **kwargs):
        from nooa.agents.summarization import summary_fork_active

        from .provider_stream import reply_channel

        purpose = (
            "reply"
            if reply_channel.get()
            else "summary"
            if summary_fork_active()
            else getattr(kwargs.get("output_model"), "__name__", "research")
        )
        reservation = self.reserve_tokens
        # UTF-8 byte count is deliberately conservative, with framing/schema headroom.
        # Unknown message/tool types fall back to the full configured context ceiling.
        try:

            def serialize(value):
                if hasattr(value, "model_json_schema"):
                    return value.model_json_schema()
                if hasattr(value, "model_dump"):
                    return value.model_dump(mode="json")
                if hasattr(value, "parameters_model"):
                    return {
                        "name": getattr(value, "name", ""),
                        "description": getattr(value, "description", ""),
                        "parameters": value.parameters_model.model_json_schema()
                        if value.parameters_model
                        else {},
                    }
                raise TypeError("Unknown request element")

            request = {
                "messages": args[0] if args else kwargs.get("messages", []),
                "tools": kwargs.get("tools"),
                "output_model": kwargs.get("output_model"),
            }
            size = len(json.dumps(request, ensure_ascii=False, default=serialize).encode()) + 8192
            output = int(
                kwargs.get("max_tokens", kwargs.get("max_completion_tokens", self.output_ceiling))
            )
            if not 0 < output <= self.output_ceiling:
                raise ValueError("Output exceeds configured ceiling")
            reservation = min(self.reserve_tokens, 2 * (size + output))
        except (TypeError, ValueError, AttributeError):
            pass
        try:
            key = self.ledger.reserve(reservation, self.events.agent_id, purpose)
        except BudgetExceeded:
            self.budget_exhausted = True
            self.events.emit("budget.exhausted", **self.ledger.snapshot())
            raise
        counter = [0]
        token = _attempts.set(counter)
        try:
            result = await super().acall(*args, **kwargs)
        except BaseException as error:
            if type(error).__name__ == "AdmissionCallCapError":
                self.budget_exhausted = True
                self.events.emit(
                    "budget.exhausted", reason="model_call_cap", **self.ledger.snapshot()
                )
            self.ledger.settle(key, dispatched=counter[0] > 0)
            self.events.emit("budget.usage", purpose=purpose, **self.ledger.snapshot())
            raise
        else:
            usage = getattr(result, "usage", None)
            if usage is not None and not (
                usage.total_tokens or usage.input_tokens + usage.output_tokens
            ):
                usage = None
            self.ledger.settle(
                key,
                usage,
                unreported_retry_tokens=max(0, counter[0] - 1) * (reservation // 2) if usage else 0,
            )
            self.events.emit("budget.usage", purpose=purpose, **self.ledger.snapshot())
            return result
        finally:
            _attempts.reset(token)

    async def aclose(self):
        self.controller.close() if callable(getattr(self.controller, "close", None)) else None
        await super().aclose()


class MissionBudget:
    def __init__(self, root, mission, policy, connection_file=None):
        self.root = root
        self.mission = mission
        self.policy = policy
        self.broker = None
        self.path = connection_file or root / "requests" / f"{mission}.admission.json"
        if connection_file is None:
            self.broker = AdmissionBroker(
                group="etch-" + mission,
                max_in_flight=policy.max_model_in_flight,
                max_calls=policy.max_model_calls,
            ).start()
            self.config = self.broker.controller_config(queue_timeout=120)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(self.config)))
            self.path.chmod(0o600)
        else:
            self.config = BrokerAdmissionConfig(**json.loads(self.path.read_text()))
        self.ledger = TokenLedger(root, mission, policy.max_total_tokens)

    def client(self, base, events, settings):
        # Includes the configured one retry. Failed requests retain this upper-bound charge.
        return BudgetedClient(
            base,
            AdmissionControlConfig(controller=self.config.controller()),
            self.ledger,
            events,
            settings.context_window * 2,
            settings.max_output_tokens,
        )

    def snapshot(self):
        data = self.ledger.snapshot()
        if self.broker:
            data.update(asdict(self.broker.snapshot()))
        return data

    def close(self):
        if self.broker:
            self.broker.close()
            self.path.unlink(missing_ok=True)
