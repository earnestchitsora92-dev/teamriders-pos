(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const currencyMeta = document.querySelector('meta[name="currency"]');
  window.APP = {
    csrf: meta ? meta.content : "",
    currency: currencyMeta ? currencyMeta.content : "$"
  };

  const menuBtn = document.getElementById("menuBtn");
  const sidebar = document.getElementById("sidebar");
  if (menuBtn && sidebar) {
    menuBtn.addEventListener("click", function () {
      sidebar.classList.toggle("open");
    });
    document.addEventListener("click", function (event) {
      if (window.innerWidth > 900) return;
      if (sidebar.contains(event.target) || menuBtn.contains(event.target)) return;
      sidebar.classList.remove("open");
    });
  }

  document.querySelectorAll("[data-flash]").forEach(function (el) {
    const close = el.querySelector(".flash-close");
    if (close) close.addEventListener("click", function () { el.remove(); });
    setTimeout(function () {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 400);
    }, 6000);
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  window.api = async function (url, data, method) {
    const response = await fetch(url, {
      method: method || "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": window.APP.csrf
      },
      credentials: "same-origin",
      body: data === undefined ? undefined : JSON.stringify(data)
    });
    let payload = {};
    try { payload = await response.json(); } catch (e) { payload = {}; }
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Session expired");
    }
    if (!response.ok) {
      throw new Error(payload.error || ("Request failed (" + response.status + ")"));
    }
    return payload;
  };

  window.fmtMoney = function (cents) {
    const value = (cents || 0) / 100;
    return window.APP.currency + value.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  };

  document.querySelectorAll(".modal-backdrop").forEach(function (backdrop) {
    backdrop.addEventListener("mousedown", function (event) {
      if (event.target === backdrop) backdrop.classList.remove("open");
    });
  });
  document.querySelectorAll("[data-close-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const modal = document.getElementById(btn.dataset.closeModal);
      if (modal) modal.classList.remove("open");
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal-backdrop.open").forEach(function (m) { m.classList.remove("open"); });
    }
  });

  window.openModal = function (id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add("open");
    return modal;
  };
})();
