from pydantic import BaseModel, ConfigDict


class Conclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_experiment_id: str
    summary: str
    limitations: list[str]
