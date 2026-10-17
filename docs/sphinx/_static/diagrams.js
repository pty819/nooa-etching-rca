// SVGs are already rendered. This optional enhancement only opens the full-size file.
document.addEventListener("DOMContentLoaded", () => {
  for (const object of document.querySelectorAll('figure object[type="image/svg+xml"]')) {
    const row = document.createElement("p");
    row.className = "diagram-actions";
    const link = document.createElement("a");
    link.href = object.getAttribute("data");
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "打开完整 SVG 图 ↗";
    row.append(link);
    object.closest("figure").append(row);
  }
});
