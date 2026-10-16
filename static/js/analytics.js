(() => {
    const selection = document.getElementById("analytics-selection");
    document.querySelectorAll("[data-period-detail]").forEach((bar) => {
        const inspect = () => {
            if (selection) selection.textContent = bar.dataset.periodDetail;
            document.querySelectorAll("[data-period-detail]").forEach((other) => {
                other.setAttribute("aria-pressed", String(other === bar));
            });
        };
        bar.setAttribute("aria-pressed", "false");
        bar.addEventListener("click", inspect);
    });
    const preset = document.getElementById("analytics-preset");
    ["analytics-start", "analytics-end"].forEach((id) => {
        const field = document.getElementById(id);
        if (field && preset) field.addEventListener("change", () => { preset.value = "custom"; });
    });
})();
