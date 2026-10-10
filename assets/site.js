/* atlas | writes: theme, text size, and the reader (progress, contents, resume). */
(function () {
  var root = document.documentElement;
  function save(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }
  function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  /* Light / dark */
  var toggle = document.querySelector('.theme-toggle');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
  function isDark() { var t = root.dataset.theme; return t ? t === 'dark' : prefersDark.matches; }
  function syncToggle() {
    if (!toggle) return;
    var dark = isDark();
    toggle.setAttribute('aria-pressed', String(dark));
    toggle.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    toggle.querySelector('.tt-label').textContent = dark ? 'Light' : 'Dark';
  }
  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = isDark() ? 'light' : 'dark';
      root.dataset.theme = next; save('theme', next); syncToggle();
    });
    if (prefersDark.addEventListener) prefersDark.addEventListener('change', syncToggle);
    syncToggle();
  }

  /* Text size */
  var scale = parseFloat(load('scale')) || 1;
  document.querySelectorAll('[data-scale]').forEach(function (b) {
    b.addEventListener('click', function () {
      scale = Math.min(1.4, Math.max(0.85, Math.round((scale + 0.075 * Number(b.dataset.scale)) * 1000) / 1000));
      root.style.setProperty('--scale', scale); save('scale', scale);
    });
  });

  /* Everything below is for story pages only */
  var story = document.querySelector('[data-story]');
  if (!story) return;
  var key = 'pos:' + story.dataset.story;
  var bar = document.querySelector('.progress span');
  var prose = story.querySelector('.prose');
  var heads = Array.prototype.slice.call(prose.querySelectorAll('h2.part, h3.chapter'));
  var tocLinks = Array.prototype.slice.call(story.querySelectorAll('.toc a'));
  var contents = story.querySelector('details.toc');
  if (contents && window.matchMedia('(min-width: 861px)').matches) contents.open = true;

  function fraction() {
    var top = prose.getBoundingClientRect().top + window.scrollY;
    var span = prose.offsetHeight - window.innerHeight * 0.6;
    return span > 0 ? Math.min(1, Math.max(0, (window.scrollY - top + window.innerHeight * 0.3) / span)) : 0;
  }
  function currentHead() {
    var found = null;
    for (var i = 0; i < heads.length; i++) {
      if (heads[i].getBoundingClientRect().top < 120) found = heads[i]; else break;
    }
    return found;
  }
  function describe(h) {
    if (!h) return '';
    if (h.classList.contains('chapter--num')) {
      var part = null;
      for (var i = heads.indexOf(h); i >= 0; i--) { if (heads[i].tagName === 'H2') { part = heads[i]; break; } }
      var n = h.querySelector('.num').textContent;
      return (part ? part.textContent.trim() + ', chapter ' : 'Chapter ') + n;
    }
    return h.textContent.trim();
  }

  var lastSave = 0, ticking = false;
  function update() {
    ticking = false;
    var f = fraction();
    if (bar) bar.style.transform = 'scaleX(' + f + ')';
    var h = currentHead();
    var id = h ? h.id : null;
    var partId = null;
    if (h) { for (var i = heads.indexOf(h); i >= 0; i--) { if (heads[i].tagName === 'H2') { partId = heads[i].id; break; } } }
    tocLinks.forEach(function (a) {
      var target = a.getAttribute('href').slice(1);
      if (target === id || (target === partId && !a.closest('.toc-ch'))) a.setAttribute('aria-current', 'location');
      else a.removeAttribute('aria-current');
    });
    var now = Date.now();
    if (now - lastSave > 1500) { lastSave = now; remember(f, h); }
  }
  function remember(f, h) {
    if (f > 0.02 && f < 0.97) save(key, JSON.stringify({ y: window.scrollY / document.documentElement.scrollHeight, where: describe(h) }));
    else if (f >= 0.97) save(key, null);
  }
  window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  window.addEventListener('pagehide', function () { remember(fraction(), currentHead()); });
  update();

  /* Offer to pick up where the reader left off */
  var box = story.querySelector('.resume');
  var saved = null;
  try { saved = JSON.parse(load(key)); } catch (e) {}
  if (box && saved && saved.y && !location.hash && window.scrollY < 50) {
    if (saved.where) box.querySelector('.resume-where').textContent = ': ' + saved.where;
    box.hidden = false;
    box.querySelector('.resume-go').addEventListener('click', function () {
      box.hidden = true;
      window.scrollTo({ top: saved.y * document.documentElement.scrollHeight });
    });
    box.querySelector('.resume-x').addEventListener('click', function () { box.hidden = true; save(key, null); });
  }
})();

/* "Copy address" button on the About page */
document.querySelectorAll('[data-copy]').forEach(function (b) {
  b.addEventListener('click', function () {
    var done = function () { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy address'; }, 2000); };
    if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy).then(done, function () { window.prompt('Copy the address:', b.dataset.copy); });
    else window.prompt('Copy the address:', b.dataset.copy);
  });
});
