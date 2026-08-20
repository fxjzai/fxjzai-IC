(() => {
  const actorId = () => {
    const storageKey = "ic.feedback.actor";
    try {
      let id = window.localStorage.getItem(storageKey);
      if (!id) {
        const suffix = window.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
        id = `web-${suffix}`;
        window.localStorage.setItem(storageKey, id);
      }
      return id;
    } catch {
      return "web-internal";
    }
  };

  document.querySelectorAll("[data-feedback]").forEach((control) => {
    const buttons = [...control.querySelectorAll("[data-feedback-label]")];
    const status = control.querySelector("[data-feedback-status]");

    buttons.forEach((button) => {
      button.addEventListener("click", async () => {
        const label = button.dataset.feedbackLabel;
        const actor = actorId();
        buttons.forEach((candidate) => {
          candidate.disabled = true;
        });
        control.classList.remove("is-error");
        status.textContent = "记录中…";

        try {
          const response = await fetch("/v1/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              actor_id: actor,
              target_kind: "digest_entry",
              target_id: control.dataset.entryId,
              digest_id: control.dataset.digestId,
              label,
              idempotency_key: `web:${actor}:${control.dataset.entryId}:${label}`,
            }),
          });
          if (!response.ok) {
            throw new Error(`feedback request failed: ${response.status}`);
          }
          button.classList.add("is-selected");
          button.setAttribute("aria-pressed", "true");
          status.textContent = `已记录：${button.textContent}`;
        } catch {
          control.classList.add("is-error");
          status.textContent = "记录失败，请重试";
          buttons.forEach((candidate) => {
            candidate.disabled = false;
          });
        }
      });
    });
  });
})();
