document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

document.querySelectorAll("[data-logo-hero]").forEach((hero) => {
  const slides = [...hero.querySelectorAll("[data-slide]")];
  const dots = [...hero.querySelectorAll("[data-logo-dot]")];
  if (!slides.length) return;

  let activeIndex = 0;
  let timer;
  const showSlide = (nextIndex) => {
    activeIndex = (nextIndex + slides.length) % slides.length;
    slides.forEach((slide, index) => {
      const active = index === activeIndex;
      slide.classList.toggle("active", active);
      slide.setAttribute("aria-hidden", String(!active));
    });
    dots.forEach((dot, index) => dot.classList.toggle("active", index === activeIndex));
    hero.style.setProperty("--slide-tone", String(activeIndex % 5));
  };
  const play = () => {
    window.clearInterval(timer);
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches && slides.length > 1) {
      timer = window.setInterval(() => showSlide(activeIndex + 1), 3600);
    }
  };

  hero.querySelector("[data-logo-previous]")?.addEventListener("click", () => {
    showSlide(activeIndex - 1);
    play();
  });
  hero.querySelector("[data-logo-next]")?.addEventListener("click", () => {
    showSlide(activeIndex + 1);
    play();
  });
  dots.forEach((dot, index) => dot.addEventListener("click", () => {
    showSlide(index);
    play();
  }));
  hero.addEventListener("mouseenter", () => window.clearInterval(timer));
  hero.addEventListener("mouseleave", play);
  play();
});

document.querySelectorAll("[data-count]").forEach((counter) => {
  const target = Number(counter.dataset.count);
  if (!Number.isFinite(target) || target === 0) return;
  const observer = new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) return;
    observer.disconnect();
    const started = performance.now();
    const tick = (now) => {
      const progress = Math.min((now - started) / 700, 1);
      counter.textContent = Math.round(target * (1 - Math.pow(1 - progress, 3))).toLocaleString("zh-CN");
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, { threshold: 0.6 });
  observer.observe(counter);
});

document.querySelectorAll(".bar-chart").forEach((chart) => {
  if (chart.dataset.series) {
    const series = JSON.parse(chart.dataset.series || "[]");
    const max = Math.max(...series.flatMap((item) => [item.started, item.finished]), 1);
    chart.innerHTML = series.map((item) => `
      <div class="bar-column telemetry-column">
        <span class="bar-value">${item.finished}/${item.started}</span>
        <div class="bar-pair">
          <div class="bar started" style="height:${Math.max((item.started / max) * 100, 3)}%" title="开始 ${item.started}"></div>
          <div class="bar finished" style="height:${Math.max((item.finished / max) * 100, 3)}%" title="完成 ${item.finished}"></div>
        </div>
        <small>${item.date}</small>
      </div>`).join("");
    return;
  }
  const values = JSON.parse(chart.dataset.values || "[]");
  const max = Math.max(...values, 1);
  const days = ["一", "二", "三", "四", "五", "六", "日"];
  chart.innerHTML = values.map((value, index) => `
    <div class="bar-column">
      <span class="bar-value">${value}</span>
      <div class="bar" style="height:${Math.max((value / max) * 100, 3)}%"></div>
      <small>周${days[index]}</small>
    </div>`).join("");
});