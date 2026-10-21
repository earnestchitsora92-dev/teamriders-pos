(function () {
  const root = document.getElementById("posRoot");
  if (!root) return;

  const mode = root.dataset.mode || "sale";
  const taxRate = parseFloat(root.dataset.taxRate || "0") || 0;
  const isAdmin = root.dataset.isAdmin === "1";
  const cart = new Map();
  let appliedDiscount = null;
  let results = [];
  let activeIndex = -1;
  let searchTimer = null;
  let oosProduct = null;

  const $ = function (id) { return document.getElementById(id); };
  const search = $("posSearch");
  const suggestions = $("suggestions");
  const cartList = $("cartList");
  const alertBox = $("posAlert");

  function showAlert(message, kind) {
    if (!alertBox) return;
    alertBox.textContent = message;
    alertBox.className = "flash " + (kind || "err");
    alertBox.style.display = message ? "flex" : "none";
    if (message) {
      clearTimeout(showAlert._t);
      showAlert._t = setTimeout(function () { alertBox.style.display = "none"; }, 6000);
    }
  }

  function subtotal() {
    let total = 0;
    cart.forEach(function (item) { total += item.price_cents * item.qty; });
    return total;
  }

  function totals() {
    const sub = subtotal();
    const discount = appliedDiscount ? Math.min(appliedDiscount.discount_cents, sub) : 0;
    const taxable = sub - discount;
    const tax = Math.round(taxable * taxRate / 100);
    return { sub: sub, discount: discount, taxable: taxable, tax: tax, total: taxable + tax };
  }

  function renderTotals() {
    const t = totals();
    $("subtotalVal").textContent = fmtMoney(t.sub);
    $("grandTotal").textContent = fmtMoney(t.total);

    const chip = $("discountChip");
    if (appliedDiscount && t.discount > 0) {
      chip.style.display = "flex";
      chip.querySelector(".chip-text").textContent =
        "Discount " + appliedDiscount.code + " −" + fmtMoney(t.discount);
    } else {
      chip.style.display = "none";
    }

    const taxRow = $("taxRow");
    if (taxRate > 0) {
      taxRow.style.display = "flex";
      $("taxVal").textContent = fmtMoney(t.tax);
      $("taxLabel").textContent = root.dataset.taxLabel + " (" + taxRate + "%)";
    } else {
      taxRow.style.display = "none";
    }
    updateChange();
    updateButtons();
  }

  function updateChange() {
    const t = totals();
    const method = document.querySelector('input[name="payMethod"]:checked');
    const isCash = !method || method.value === "cash";
    $("tenderWrap").style.display = isCash ? "block" : "none";
    const tendered = Math.round(parseFloat($("tendered").value || "0") * 100);
    const change = isCash && tendered > t.total ? tendered - t.total : 0;
    $("changeVal").textContent = fmtMoney(change);
    $("changeBox").className = "change-box" + (isCash && tendered > 0 && tendered < t.total ? " short" : "");
  }

  function updateButtons() {
    const empty = cart.size === 0;
    $("checkoutBtn").disabled = empty;
    $("quoteBtn").disabled = empty;
    $("discountBtn").disabled = empty;
    $("clearBtn").disabled = empty;
  }

  function renderCart() {
    cartList.innerHTML = "";
    if (cart.size === 0) {
      const div = document.createElement("div");
      div.className = "cart-empty";
      div.innerHTML = '<span class="big">🛒</span>Search for a part to start ' + (mode === "quote" ? "a quotation" : "a sale");
      cartList.appendChild(div);
      renderTotals();
      return;
    }
    cart.forEach(function (item) {
      const row = document.createElement("div");
      row.className = "cart-row";
      row.dataset.id = item.id;

      const info = document.createElement("div");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = item.sku + " · " + fmtMoney(item.price_cents) + " each" +
        (mode === "sale" ? " · " + item.stock + " in stock" : "");
      info.appendChild(name);
      info.appendChild(meta);

      const qtyBox = document.createElement("div");
      qtyBox.className = "qty-box";
      const dec = document.createElement("button");
      dec.type = "button";
      dec.dataset.act = "dec";
      dec.textContent = "−";
      const input = document.createElement("input");
      input.type = "number";
      input.min = "1";
      input.value = item.qty;
      input.dataset.act = "qty";
      const inc = document.createElement("button");
      inc.type = "button";
      inc.dataset.act = "inc";
      inc.textContent = "+";
      qtyBox.appendChild(dec);
      qtyBox.appendChild(input);
      qtyBox.appendChild(inc);

      const lineTotal = document.createElement("div");
      lineTotal.className = "cart-line-total";
      lineTotal.textContent = fmtMoney(item.price_cents * item.qty);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-line";
      remove.dataset.act = "remove";
      remove.textContent = "×";
      remove.title = "Remove item";

      row.appendChild(info);
      row.appendChild(qtyBox);
      row.appendChild(lineTotal);
      row.appendChild(remove);
      cartList.appendChild(row);
    });
    renderTotals();
  }

  function stockLimit(item) {
    return mode === "quote" ? 999 : item.stock;
  }

  function addProduct(product) {
    if (mode === "sale" && product.stock <= 0) {
      openOutOfStock(product);
      return;
    }
    const existing = cart.get(product.id);
    if (existing) {
      if (existing.qty + 1 > stockLimit(existing)) {
        showAlert("Only " + existing.stock + " unit(s) of " + existing.name + " in stock.");
        return;
      }
      existing.qty += 1;
    } else {
      cart.set(product.id, {
        id: product.id,
        sku: product.sku,
        name: product.name,
        price_cents: product.price_cents,
        stock: product.stock,
        qty: 1
      });
    }
    renderCart();
  }

  function changeQty(id, delta) {
    const item = cart.get(id);
    if (!item) return;
    item.qty += delta;
    if (item.qty <= 0) {
      cart.delete(id);
    } else if (item.qty > stockLimit(item)) {
      item.qty = stockLimit(item);
      showAlert("Only " + item.stock + " unit(s) available.");
    }
    renderCart();
  }

  /* ---------------------------------------------------------- search */
  function renderSuggestions(items) {
    results = items;
    activeIndex = -1;
    suggestions.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "sug-empty";
      empty.textContent = "No parts match that search.";
      suggestions.appendChild(empty);
      suggestions.classList.add("open");
      return;
    }
    items.forEach(function (p, idx) {
      const el = document.createElement("div");
      el.className = "sug";
      el.dataset.idx = idx;

      const info = document.createElement("div");
      const name = document.createElement("div");
      name.className = "sug-name";
      name.textContent = p.name;
      const meta = document.createElement("div");
      meta.className = "sug-meta";
      meta.textContent = p.sku + " · " + (p.brand || p.category) + (p.compatible ? " · " + p.compatible : "");
      info.appendChild(name);
      info.appendChild(meta);

      const price = document.createElement("div");
      price.className = "sug-price";
      price.textContent = fmtMoney(p.price_cents);
      const stock = document.createElement("div");
      stock.className = "sug-stock " + (p.stock <= 0 ? "out" : (p.stock <= p.reorder_level ? "low" : "in"));
      stock.textContent = p.stock <= 0 ? "Out of stock" : (p.stock + " in stock");
      price.appendChild(stock);

      el.appendChild(info);
      el.appendChild(price);
      el.addEventListener("mousedown", function (event) {
        event.preventDefault();
        chooseProduct(p);
      });
      suggestions.appendChild(el);
    });
    suggestions.classList.add("open");
  }

  function chooseProduct(product) {
    addProduct(product);
    search.value = "";
    suggestions.classList.remove("open");
    search.focus();
  }

  function doSearch(term) {
    fetch("/api/products/search?q=" + encodeURIComponent(term), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) { renderSuggestions(data.items || []); })
      .catch(function () { suggestions.classList.remove("open"); });
  }

  if (search) {
    search.addEventListener("input", function () {
      const term = search.value.trim();
      clearTimeout(searchTimer);
      if (!term) { suggestions.classList.remove("open"); return; }
      searchTimer = setTimeout(function () { doSearch(term); }, 120);
    });
    search.addEventListener("keydown", function (event) {
      const nodes = suggestions.querySelectorAll(".sug");
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!nodes.length) return;
        event.preventDefault();
        activeIndex += event.key === "ArrowDown" ? 1 : -1;
        if (activeIndex < 0) activeIndex = nodes.length - 1;
        if (activeIndex >= nodes.length) activeIndex = 0;
        nodes.forEach(function (n, i) { n.classList.toggle("active", i === activeIndex); });
        nodes[activeIndex].scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter") {
        event.preventDefault();
        const pick = results[activeIndex] || results[0];
        if (pick) chooseProduct(pick);
      } else if (event.key === "Escape") {
        suggestions.classList.remove("open");
      }
    });
    search.addEventListener("blur", function () {
      setTimeout(function () { suggestions.classList.remove("open"); }, 150);
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "F2") {
      event.preventDefault();
      if (search) { search.focus(); search.select(); }
    }
  });

  /* ---------------------------------------------------------- cart events */
  cartList.addEventListener("click", function (event) {
    const btn = event.target.closest("[data-act]");
    if (!btn) return;
    const row = btn.closest(".cart-row");
    if (!row) return;
    const id = Number(row.dataset.id);
    const action = btn.dataset.act;
    if (action === "inc") changeQty(id, 1);
    if (action === "dec") changeQty(id, -1);
    if (action === "remove") { cart.delete(id); renderCart(); }
  });
  cartList.addEventListener("change", function (event) {
    const input = event.target.closest('input[data-act="qty"]');
    if (!input) return;
    const row = input.closest(".cart-row");
    const id = Number(row.dataset.id);
    const item = cart.get(id);
    if (!item) return;
    let qty = parseInt(input.value, 10);
    if (isNaN(qty) || qty < 1) qty = 1;
    if (qty > stockLimit(item)) {
      qty = stockLimit(item);
      showAlert("Only " + item.stock + " unit(s) available.");
    }
    item.qty = qty;
    renderCart();
  });

  /* ---------------------------------------------------------- totals actions */
  $("discountBtn").addEventListener("click", function () {
    $("discountError").style.display = "none";
    $("discountForm").reset();
    $("discountAdminFields").style.display = isAdmin ? "none" : "block";
    $("discountSelfNote").style.display = isAdmin ? "block" : "none";
    openModal("discountModal");
    setTimeout(function () { $("discountValue").focus(); }, 80);
  });

  $("discountForm").addEventListener("submit", async function (event) {
    event.preventDefault();
    const errorBox = $("discountError");
    errorBox.style.display = "none";
    const payload = {
      kind: $("discountType").value,
      value: $("discountType").value === "amount" ? $("discountValue").value : parseFloat($("discountValue").value),
      reason: $("discountReason").value,
      admin_username: $("discountAdminUser").value,
      admin_password: $("discountAdminPass").value,
      subtotal_cents: subtotal()
    };
    try {
      const result = await api("/api/discounts", payload);
      if (result.status === "approved") {
        appliedDiscount = { code: result.code, discount_cents: result.discount_cents };
        closeModal("discountModal");
        renderTotals();
        showAlert("Discount " + result.code + " applied.", "ok");
      } else {
        errorBox.style.display = "block";
        errorBox.className = "login-error";
        errorBox.innerHTML = "";
        const text = document.createElement("div");
        text.textContent = "Approval requested. Code: " + result.code + ". Once an administrator approves it, enter the code below to apply it.";
        errorBox.appendChild(text);
        $("discountCodeInput").value = result.code;
      }
    } catch (err) {
      errorBox.style.display = "block";
      errorBox.className = "login-error";
      errorBox.textContent = err.message;
    }
  });

  $("applyCodeBtn").addEventListener("click", async function () {
    const code = $("discountCodeInput").value.trim().toUpperCase();
    if (!code) return;
    try {
      const data = await api("/api/discounts/" + encodeURIComponent(code), undefined, "GET");
      appliedDiscount = { code: data.code, discount_cents: data.discount_cents };
      renderTotals();
      showAlert("Discount " + data.code + " applied (" + fmtMoney(data.discount_cents) + ").", "ok");
      $("discountCodeInput").value = "";
    } catch (err) {
      showAlert(err.message);
    }
  });

  $("discountCodeInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); $("applyCodeBtn").click(); }
  });

  document.querySelectorAll(".chip-remove").forEach(function (btn) {
    btn.addEventListener("click", function () {
      appliedDiscount = null;
      renderTotals();
    });
  });

  $("clearBtn").addEventListener("click", function () {
    if (cart.size && !window.confirm("Clear the cart?")) return;
    cart.clear();
    appliedDiscount = null;
    renderCart();
  });

  $("tendered").addEventListener("input", updateChange);
  $("exactBtn").addEventListener("click", function () {
    $("tendered").value = (totals().total / 100).toFixed(2);
    updateChange();
  });
  document.querySelectorAll('input[name="payMethod"]').forEach(function (radio) {
    radio.addEventListener("change", updateChange);
  });

  /* ---------------------------------------------------------- checkout */
  function payload() {
    return {
      items: Array.from(cart.values()).map(function (i) { return { product_id: i.id, qty: i.qty }; }),
      discount_code: appliedDiscount ? appliedDiscount.code : "",
      customer_name: $("customerName").value,
      customer_phone: $("customerPhone").value,
      customer_email: $("customerEmail").value,
      payment_method: (document.querySelector('input[name="payMethod"]:checked') || {}).value || "cash",
      tendered: $("tendered").value,
      note: $("saleNote").value
    };
  }

  function busy(button, state, label) {
    if (state) {
      button.dataset.original = button.textContent;
      button.textContent = label;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.original || button.textContent;
      button.disabled = false;
    }
  }

  $("checkoutBtn").addEventListener("click", async function () {
    if (cart.size === 0) return;
    const t = totals();
    const method = (document.querySelector('input[name="payMethod"]:checked') || {}).value || "cash";
    const tendered = Math.round(parseFloat($("tendered").value || "0") * 100);
    if (method === "cash" && tendered > 0 && tendered < t.total) {
      showAlert("Amount tendered is less than the total due.");
      return;
    }
    const button = this;
    busy(button, true, "Completing…");
    try {
      const sale = await api("/api/sales", payload());
      $("successTitle").textContent = "Sale completed";
      $("successReceipt").textContent = sale.receipt_no;
      $("successTotal").textContent = fmtMoney(sale.total_cents);
      $("successChange").textContent = fmtMoney(sale.change_cents);
      $("successChangeRow").style.display = sale.payment_method === "cash" ? "flex" : "none";
      $("printBtn").dataset.url = sale.receipt_url + "?autoprint=1";
      $("pdfBtn").dataset.url = sale.pdf_url + "?size=thermal";
      $("viewBtn").dataset.url = sale.view_url;
      openModal("successModal");
      cart.clear();
      appliedDiscount = null;
      $("customerName").value = "";
      $("customerPhone").value = "";
      $("customerEmail").value = "";
      $("saleNote").value = "";
      $("tendered").value = "";
      renderCart();
      search.focus();
    } catch (err) {
      showAlert(err.message);
    } finally {
      busy(button, false);
    }
  });

  $("quoteBtn").addEventListener("click", async function () {
    if (cart.size === 0) return;
    const button = this;
    busy(button, true, "Saving…");
    try {
      const quote = await api("/api/quotations", payload());
      $("successTitle").textContent = "Quotation saved";
      $("successReceipt").textContent = quote.quote_no;
      $("successTotal").textContent = fmtMoney(quote.total_cents);
      $("successChangeRow").style.display = "none";
      $("printBtn").dataset.url = quote.pdf_url;
      $("pdfBtn").dataset.url = quote.pdf_url;
      $("viewBtn").dataset.url = quote.view_url;
      openModal("successModal");
      cart.clear();
      appliedDiscount = null;
      renderCart();
    } catch (err) {
      showAlert(err.message);
    } finally {
      busy(button, false);
    }
  });

  document.querySelectorAll("#successModal .js-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (btn.dataset.url) window.open(btn.dataset.url, "_blank");
    });
  });
  $("newSaleBtn").addEventListener("click", function () {
    closeModal("successModal");
    search.focus();
  });

  /* ---------------------------------------------------------- out of stock */
  function openOutOfStock(product) {
    oosProduct = product;
    $("oosProductName").textContent = product.name;
    $("oosPrice").textContent = fmtMoney(product.price_cents);
    $("oosForm").reset();
    $("oosSuccess").style.display = "none";
    $("oosError").style.display = "none";
    openModal("oosModal");
    setTimeout(function () { $("oosEmail").focus(); }, 80);
  }

  $("oosForm").addEventListener("submit", async function (event) {
    event.preventDefault();
    const errorBox = $("oosError");
    errorBox.style.display = "none";
    if (!oosProduct) return;
    try {
      await api("/api/alerts", {
        product_id: oosProduct.id,
        email: $("oosEmail").value,
        customer_name: $("oosName").value,
        phone: $("oosPhone").value
      });
      $("oosSuccess").style.display = "block";
      $("oosSuccess").textContent = "Saved. " + $("oosEmail").value + " will be emailed when this part is back in stock.";
      $("oosForm").reset();
      setTimeout(function () { closeModal("oosModal"); }, 2200);
    } catch (err) {
      errorBox.style.display = "block";
      errorBox.className = "login-error";
      errorBox.textContent = err.message;
    }
  });

  function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove("open");
  }

  /* ---------------------------------------------------------- prefill */
  const prefillNode = document.getElementById("posPrefill");
  if (prefillNode) {
    try {
      const prefill = JSON.parse(prefillNode.textContent);
      if (prefill.customer_name) $("customerName").value = prefill.customer_name;
      if (prefill.customer_phone) $("customerPhone").value = prefill.customer_phone;
      if (prefill.customer_email) $("customerEmail").value = prefill.customer_email;
      (prefill.items || []).forEach(function (item) {
        cart.set(item.product_id, {
          id: item.product_id,
          sku: item.sku,
          name: item.name,
          price_cents: item.price_cents,
          stock: 999,
          qty: item.qty
        });
      });
      if ((prefill.items || []).length) {
        showAlert("Quotation loaded into the cart. Review prices before completing.", "ok");
      }
    } catch (e) { /* ignore malformed prefill */ }
  }

  renderCart();
  if (search) search.focus();
})();
