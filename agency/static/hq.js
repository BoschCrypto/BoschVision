// HQ: keep agent status live without reloading, and copy-to-clipboard buttons.
(function () {
  "use strict";

  const cards = document.querySelectorAll("[data-agent]");
  async function refresh() {
    try {
      const res = await fetch("/hq/api/status", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      cards.forEach((card) => {
        const a = data.agents[card.dataset.agent];
        if (!a) return;
        card.querySelectorAll("[data-field]").forEach((el) => {
          const field = el.dataset.field;
          if (!(field in a)) return;
          el.textContent = a[field];
          if (field === "status") el.className = "status s-" + a.status;
        });
      });
      const badge = document.querySelector('.top nav a[href="/hq/approvals"] .count');
      if (badge) badge.textContent = data.pending;
    } catch (_) { /* offline for a moment: try again next tick */ }
  }
  if (cards.length) setInterval(refresh, 8000);

  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const src = document.getElementById(btn.dataset.copy);
      if (!src) return;
      try {
        await navigator.clipboard.writeText(src.value);
      } catch (_) {
        src.select();
        document.execCommand("copy");
      }
      const label = btn.textContent;
      btn.textContent = "Copied ✓";
      setTimeout(() => { btn.textContent = label; }, 1500);
    });
  });
})();
