// Dvojity posuvnik (min-max) pro filtry ceny, plochy a ceny za m2.
(function () {
  "use strict";

  function fmt(n, unit) {
    var s = Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return s + " " + unit;
  }

  function initRange(box) {
    var unit = box.dataset.unit || "";
    var lo = parseFloat(box.dataset.min);
    var hi = parseFloat(box.dataset.max);
    var minInput = box.querySelector(".range-min");
    var maxInput = box.querySelector(".range-max");
    var fill = box.querySelector(".range-fill");
    var out = box.querySelector(".range-out");
    if (!minInput || !maxInput) return;

    function pct(v) { return hi > lo ? ((v - lo) / (hi - lo)) * 100 : 0; }

    function render() {
      var a = parseFloat(minInput.value);
      var b = parseFloat(maxInput.value);
      // Nedovolit prekriti thumbu.
      if (a > b) {
        if (document.activeElement === minInput) { a = b; minInput.value = b; }
        else { b = a; maxInput.value = a; }
      }
      fill.style.left = pct(a) + "%";
      fill.style.width = (pct(b) - pct(a)) + "%";
      var loTxt = a <= lo ? "od " + fmt(lo, unit) : "od " + fmt(a, unit);
      var hiTxt = b >= hi ? "do " + fmt(hi, unit) + "+" : "do " + fmt(b, unit);
      // Kdyz je plny rozsah, nabidnout srozumitelny popis.
      if (a <= lo && b >= hi) out.textContent = "vše (" + fmt(lo, unit) + " – " + fmt(hi, unit) + ")";
      else out.textContent = loTxt + " " + hiTxt;
      // Aktivni thumb navrch, at jde chytit i pri prekryvu.
      minInput.style.zIndex = (a > hi - (hi - lo) * 0.05) ? 5 : 3;
    }

    minInput.addEventListener("input", render);
    maxInput.addEventListener("input", render);
    render();
  }

  // Interaktivní tooltip pro grafy ČSÚ (hodnota po najetí myší).
  function initCsu(box) {
    var svg = box.querySelector(".csu-chart");
    if (!svg) return;
    var guide = svg.querySelector(".csu-guide");
    var dots = svg.querySelectorAll(".csu-dot");
    var tip = document.createElement("div");
    tip.className = "csu-tip";
    tip.hidden = true;
    box.appendChild(tip);

    function clear() {
      tip.hidden = true;
      dots.forEach(function (d) { d.classList.remove("on"); });
      if (guide) guide.setAttribute("opacity", "0");
    }

    box.querySelectorAll(".csu-hz").forEach(function (z) {
      z.addEventListener("mouseenter", function () {
        var i = z.dataset.i;
        tip.textContent = "Rok " + z.dataset.year + ": " + z.dataset.val + " Kč/m²";
        tip.hidden = false;
        dots.forEach(function (d) { d.classList.remove("on"); });
        var dot = svg.querySelector('.csu-dot[data-i="' + i + '"]');
        if (dot) {
          dot.classList.add("on");
          if (guide) {
            guide.setAttribute("x1", dot.getAttribute("cx"));
            guide.setAttribute("x2", dot.getAttribute("cx"));
            guide.setAttribute("opacity", "0.45");
          }
        }
      });
      z.addEventListener("mousemove", function (e) {
        var r = box.getBoundingClientRect();
        tip.style.left = (e.clientX - r.left) + "px";
        tip.style.top = (e.clientY - r.top - 34) + "px";
      });
      z.addEventListener("mouseleave", clear);
    });
  }

  // Dojizdeni: ulozi cilovou zastavku do prohlizece a u kazde nabidky slozi odkaz do
  // IDOSu (nejblizsi zastavka bytu -> zadana cilova zastavka). Nic se nepocita na serveru.
  function initDojezd() {
    var input = document.getElementById("dojezd-cil");
    if (!input) return;
    var KEY = "dojezd_cil";
    var note = document.getElementById("dojezd-note");
    try { input.value = localStorage.getItem(KEY) || ""; } catch (e) {}

    function update() {
      var cil = (input.value || "").trim();
      try { localStorage.setItem(KEY, cil); } catch (e) {}
      document.querySelectorAll(".doprava").forEach(function (d) {
        var from = d.getAttribute("data-from") || "";
        var a = d.querySelector(".dopr-idos");
        var hint = d.querySelector(".dopr-hint");
        if (cil && from) {
          a.href = "https://idos.cz/vlakyautobusymhdvse/spojeni/vysledky?f=" +
            encodeURIComponent(from) + "&fc=0&t=" + encodeURIComponent(cil) + "&tc=0";
          a.hidden = false;
          if (hint) hint.hidden = true;
        } else {
          a.hidden = true;
          if (hint) hint.hidden = false;
        }
      });
      if (note) note.textContent = cil ? "cíl: " + cil : "";
    }

    input.addEventListener("input", update);
    update();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".range").forEach(initRange);
    document.querySelectorAll(".csu-box").forEach(initCsu);
    initDojezd();
  });
})();
