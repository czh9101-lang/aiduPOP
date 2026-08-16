/**
 * aiduPOP Visual Studio — random hexagon backdrop (aiduMEI DNA).
 * Three brand colors (deep blue / gray / black), random size/position/rotation.
 */
(function () {
  const BRAND_COLORS = ["#1f4e79", "#525252", "#000000"];

  function createHexBackground(selector, count, colors, opacity) {
    const container = document.querySelector(selector);
    if (!container) return;
    container.innerHTML = "";

    const palette = [];
    const per = Math.floor(count / colors.length);
    colors.forEach(c => { for (let i = 0; i < per; i++) palette.push(c); });
    while (palette.length < count) palette.push(colors[palette.length % colors.length]);
    for (let i = palette.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = palette[i]; palette[i] = palette[j]; palette[j] = t;
    }

    const frag = document.createDocumentFragment();
    for (let i = 0; i < count; i++) {
      const left = Math.random() * 100;
      const top = Math.random() * 100;
      const size = 15 + Math.random() * 70;
      const height = size * 1.1547;
      const rot = Math.floor(Math.random() * 61) - 30;
      const stroke = (0.3 + Math.random() * 0.5).toFixed(1);

      const el = document.createElement("div");
      el.className = "hex-bg-item";
      el.style.cssText = "left:" + left + "%; top:" + top + "%; width:" + size +
        "px; height:" + height + "px; transform:rotate(" + rot + "deg); opacity:" + opacity + ";";
      el.innerHTML = '<svg viewBox="0 0 100 115.47"><polygon points="50,0 100,28.87 100,86.6 50,115.47 0,86.6 0,28.87" fill="none" stroke="' +
        palette[i] + '" stroke-width="' + stroke + '"/></svg>';
      frag.appendChild(el);
    }
    container.appendChild(frag);
  }

  window.addEventListener("DOMContentLoaded", () => {
    createHexBackground("#hexBg", 480, BRAND_COLORS, 0.28);
  });
})();
