'use strict';

const $ = (id) => document.getElementById(id);
let inputMode = 'form';
let outputView = 'preview';
let examples = [];
let lastResult = null;
let resultNotice = '';
let busy = false;
let timer;
let toastTimer;
let seoDefaults = null;
const seoFields = {
  title_min: 'seo-title-min', title_max: 'seo-title-max',
  description_min: 'seo-description-min', description_max: 'seo-description-max'
};

function fillSeoLimits(limits) {
  for (const [key, id] of Object.entries(seoFields)) $(id).value = limits[key];
}

function readSeoLimits() {
  if (!seoDefaults) throw new Error('Настройки размеров не загружены. Перезапустите приложение и обновите страницу.');
  const limits = {};
  for (const [key, id] of Object.entries(seoFields)) {
    const input = $(id);
    const value = input.valueAsNumber;
    if (!Number.isInteger(value) || value < 1 || value > Number(input.max)) {
      $('seo-options').open = true;
      throw new Error(`Размеры текста: укажите целое число от 1 до ${input.max}.`);
    }
    limits[key] = value;
  }
  if (limits.title_min > limits.title_max || limits.description_min > limits.description_max) {
    $('seo-options').open = true;
    throw new Error('Минимальная длина текста не должна превышать максимальную.');
  }
  return limits;
}

async function loadSeoDefaults() {
  try {
    const response = await fetch('/api/settings');
    if (!response.ok) throw new Error('settings');
    const settings = await response.json();
    for (const [key, id] of Object.entries(seoFields)) {
      if (!Number.isInteger(settings.seo_limits?.[key])) throw new Error('settings');
      const cap = key.startsWith('title') ? settings.bounds?.title_max : settings.bounds?.description_max;
      if (!Number.isInteger(cap)) throw new Error('bounds');
      $(id).max = cap;
    }
    seoDefaults = Object.freeze({...settings.seo_limits});
    fillSeoLimits(seoDefaults);
    $('generate').disabled = busy;
  } catch {
    showError('Не удалось загрузить настройки размеров. Перезапустите приложение через start.bat и обновите страницу.');
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(message = '') {
  $('page-error').textContent = message;
  $('page-error').hidden = !message;
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3000);
}

function changed() {
  if (lastResult) $('stale-notice').hidden = false;
}

function addSpec(key = '', value = '') {
  if ($('spec-rows').children.length >= 50) return toast('Можно добавить до 50 характеристик');
  const row = element('div', 'spec-row');
  const keyInput = element('input');
  keyInput.value = key;
  keyInput.placeholder = 'Параметр';
  keyInput.maxLength = 300;
  keyInput.setAttribute('aria-label', 'Название характеристики');
  const valueInput = element('input');
  valueInput.value = value ?? '';
  valueInput.placeholder = 'Значение';
  valueInput.maxLength = 1000;
  valueInput.setAttribute('aria-label', 'Значение характеристики');
  const remove = element('button', 'remove-spec', '×');
  remove.type = 'button';
  remove.setAttribute('aria-label', 'Удалить характеристику');
  remove.addEventListener('click', () => { row.remove(); changed(); });
  row.append(keyInput, valueInput, remove);
  $('spec-rows').append(row);
}

function readForm() {
  const specs = Object.create(null);
  const keys = new Set();
  for (const row of $('spec-rows').children) {
    const [keyInput, valueInput] = row.querySelectorAll('input');
    const key = keyInput.value.trim();
    const value = valueInput.value.trim();
    if (!key && !value) continue;
    if (!key) throw new Error('Укажите название характеристики для заполненного значения.');
    if (keys.has(key.toLowerCase())) throw new Error(`Характеристика «${key}» повторяется.`);
    keys.add(key.toLowerCase());
    specs[key] = value;
  }
  return {name: $('name').value.trim(), manufacturer: $('manufacturer').value.trim(),
    specs, description: $('description').value.trim()};
}

function readJSON() {
  let product;
  try { product = JSON.parse($('json-input').value); }
  catch { throw new Error('Некорректный JSON. Проверьте кавычки, запятые и скобки.'); }
  if (!product || typeof product !== 'object' || Array.isArray(product)) {
    throw new Error('В JSON должен быть объект одного товара.');
  }
  const allowed = new Set(['name', 'manufacturer', 'specs', 'description']);
  for (const key of Object.keys(product)) {
    if (!allowed.has(key)) throw new Error(`Неизвестное поле JSON: ${key}`);
  }
  for (const key of ['name', 'manufacturer', 'description']) {
    if (key in product && typeof product[key] !== 'string') throw new Error(`Поле ${key} должно быть строкой.`);
  }
  if ('specs' in product && (!product.specs || typeof product.specs !== 'object' || Array.isArray(product.specs))) {
    throw new Error('Поле specs должно быть объектом.');
  }
  if (Object.keys(product.specs || {}).length > 50) throw new Error('Допустимо до 50 характеристик.');
  for (const [key, value] of Object.entries(product.specs || {})) {
    if (typeof value !== 'string' && value !== null) throw new Error(`Значение «${key}» должно быть строкой или null.`);
  }
  return product;
}

function populateForm(product) {
  $('name').value = product.name || '';
  $('manufacturer').value = product.manufacturer || '';
  $('description').value = product.description || '';
  $('spec-rows').replaceChildren();
  const pairs = Object.entries(product.specs || {});
  if (!pairs.length) addSpec();
  else pairs.forEach(([key, value]) => addSpec(key, value));
}

function updateDemoNotice(product) {
  const example = examples.find((entry) => entry.product.name === product.name);
  $('demo-notice').textContent = example?.notice || '';
  $('demo-notice').hidden = !example;
  return example?.notice || '';
}

function setMode(mode) {
  if (mode === inputMode || busy) return;
  try {
    if (mode === 'json') $('json-input').value = JSON.stringify(readForm(), null, 2);
    else populateForm(readJSON());
    inputMode = mode;
    $('form-fields').hidden = mode !== 'form';
    $('json-fields').hidden = mode !== 'json';
    $('name').required = mode === 'form';
    for (const item of ['form', 'json']) {
      $(`${item}-mode`).classList.toggle('active', mode === item);
      $(`${item}-mode`).setAttribute('aria-pressed', String(mode === item));
    }
    showError();
  } catch (error) { showError(error.message); }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Скопировано');
  } catch { toast('Не удалось скопировать. Скачайте файл или выделите текст в JSON / Markdown.'); }
}

function exportedMarkdown() {
  return (resultNotice ? `> ${resultNotice}\n\n` : '') + lastResult.markdown;
}

function exportedJSON() {
  return JSON.stringify({...lastResult, ...(resultNotice ? {demo_notice: resultNotice} : {})}, null, 2);
}

function switchView(view) {
  outputView = view;
  document.querySelectorAll('[data-view]').forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('preview').hidden = view !== 'preview';
  $('raw-output').hidden = view === 'preview';
  if (lastResult && view !== 'preview') {
    $('raw-output').value = view === 'json' ? exportedJSON() : exportedMarkdown();
  }
}

function copySection(label, value, options = {}) {
  const section = element('section', 'copy-section');
  const heading = element('div', 'section-label');
  const title = element('h3', '', label);
  heading.append(title);
  if (options.count) {
    const count = [...value].length;
    const invalid = count < options.count[0] || count > options.count[1];
    const counter = element('span', `char-count${invalid ? ' invalid' : ''}`, `${count} · ${options.count[0]}–${options.count[1]}`);
    counter.title = `${count} символов. Допустимый диапазон: ${options.count[0]}–${options.count[1]}.`;
    heading.append(counter);
  }
  const copy = element('button', 'section-copy', 'Копировать');
  copy.type = 'button';
  copy.setAttribute('aria-label', `Копировать: ${label}`);
  copy.addEventListener('click', () => copyText(value));
  heading.append(copy);
  section.append(heading, element('p', options.title ? 'title-copy' : 'body-copy', value));
  return section;
}

function renderResult(result) {
  lastResult = result;
  const card = result.card;
  const limits = result.applied_limits;
  const preview = $('preview');
  preview.replaceChildren();
  preview.append(copySection('SEO-заголовок', card.seo_title, {title: true, count: [limits.title_min, limits.title_max]}));
  preview.append(copySection('SEO-описание', card.seo_description, {count: [limits.description_min, limits.description_max]}));
  const specs = element('section', 'copy-section');
  const specsLabel = element('div', 'section-label');
  specsLabel.append(element('h3', '', 'Ключевые характеристики'));
  specs.append(specsLabel);
  const wrapper = element('div', 'table-wrapper');
  const table = element('table', 'spec-table');
  const tbody = element('tbody');
  for (const item of card.characteristics) {
    const tr = element('tr');
    const th = element('th', '', item.label);
    th.scope = 'row';
    const td = element('td', '', item.value);
    if (item.evidence) td.title = `Источник (${item.source}): ${item.evidence}`;
    tr.append(th, td);
    tbody.append(tr);
  }
  table.append(tbody);
  wrapper.append(table);
  specs.append(wrapper);
  const details = element('details', 'fact-details');
  details.append(element('summary', '', 'Показать источники подтверждённых фактов'));
  for (const fact of card.facts) {
    details.append(element('p', '', `${fact.id} · ${fact.label} · ${fact.source}\n«${fact.evidence}»`));
  }
  specs.append(details);
  preview.append(specs);
  const queries = element('section', 'copy-section');
  const queriesLabel = element('div', 'section-label');
  queriesLabel.append(element('h3', '', 'Поисковые запросы'));
  const chips = element('div', 'query-list');
  card.search_queries.forEach((query) => chips.append(element('span', 'query-chip', query)));
  queries.append(queriesLabel, chips);
  preview.append(queries, copySection('Для менеджера', card.commercial_block));
  const review = element('section', 'copy-section review-section');
  const reviewLabel = element('div', 'section-label');
  reviewLabel.append(element('h3', '', 'Перед публикацией'));
  const list = element('ul', 'review-list');
  card.conflicts.forEach((conflict) => list.append(element('li', '',
    `Противоречие — ${conflict.field}: ${conflict.values.join(' / ')}. Уточните по паспорту.`)));
  if (card.missing_fields.length) list.append(element('li', '', `Данных нет: ${card.missing_fields.join(', ')}.`));
  card.warnings.forEach((warning) => list.append(element('li', '', warning)));
  review.append(reviewLabel, list);
  preview.append(review);
  $('draft-badge').textContent = card.status === 'needs_review' ? 'ЕСТЬ УТОЧНЕНИЯ' : 'ЧЕРНОВИК';
  $('draft-badge').classList.toggle('review', card.status === 'needs_review');
  $('result-meta').textContent = `Подготовлено за ${result.elapsed_seconds} сек.`;
  $('result-demo').textContent = resultNotice;
  $('result-demo').hidden = !resultNotice;
  $('stale-notice').hidden = true;
  $('empty-state').hidden = true;
  $('result').hidden = false;
  $('copy-all').disabled = false;
  switchView(outputView);
}

async function checkConnection() {
  $('connection').disabled = true;
  $('model-select').disabled = true;
  $('connection-label').textContent = 'Проверяем…';
  const status = $('connection-result');
  status.hidden = true;
  try {
    const selected = $('model-select').value;
    const query = selected ? `?${new URLSearchParams({model: selected})}` : '';
    const response = await fetch(`/api/health${query}`);
    const health = await response.json();
    const ready = response.ok && health.connected && health.model_available;
    if (health.connected) {
      const models = health.available_models || [];
      const target = health.model || '';
      const select = $('model-select');
      select.replaceChildren();
      const auto = element('option', '', 'Из конфигурации / автовыбор');
      auto.value = '';
      select.append(auto);
      for (const id of models) {
        const option = element('option', '', id);
        option.value = id;
        select.append(option);
      }
      if (target && !models.includes(target)) {
        const missing = element('option', '', `${target} (нет в списке)`);
        missing.value = target;
        select.append(missing);
      }
      select.value = target;
    }
    $('connection-dot').className = `dot ${ready ? 'online' : 'offline'}`;
    status.className = `message ${ready ? 'success' : health.connected ? 'notice' : 'error'}`;
    status.textContent = ready ? `API доступен. Модель: ${health.model}.` : health.selection_required
      ? 'API доступен. Выберите модель в форме.' : health.connected && !health.available_models?.length
      ? 'API доступен, но список моделей пуст.' : health.connected
      ? `API доступен, но модель «${health.model}» отсутствует в списке. Выберите другую модель или проверьте LLM_MODEL в .env.`
      : health.error || 'Не удалось подключиться к API модели.';
  } catch {
    $('connection-dot').className = 'dot offline';
    status.className = 'message error';
    status.textContent = 'Сервер приложения недоступен. Проверьте, что он запущен.';
  } finally {
    $('connection').disabled = busy;
    $('model-select').disabled = false;
    $('connection-label').textContent = 'Проверить соединение';
    status.hidden = false;
  }
}

function setBusy(value) {
  busy = value;
  $('input-fields').disabled = value;
  for (const id of ['generate', 'form-mode', 'json-mode', 'example-select', 'connection', 'copy-all', 'download-json', 'download-md']) {
    $(id).disabled = value || (id === 'generate' && !seoDefaults) || (id === 'copy-all' && !lastResult);
  }
  $('generate-label').textContent = value ? 'Подготавливаем…' : 'Подготовить карточку';
  $('loading-state').hidden = !value;
  document.querySelector('.output-panel').setAttribute('aria-busy', String(value));
  if (value) {
    $('result').hidden = true;
    $('empty-state').hidden = true;
    const started = Date.now();
    $('elapsed').textContent = '0 сек.';
    timer = setInterval(() => { $('elapsed').textContent = `${Math.floor((Date.now() - started) / 1000)} сек.`; }, 1000);
    if (matchMedia('(max-width: 760px)').matches) {
      document.querySelector('.output-panel').scrollIntoView({
        behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start'
      });
    }
  } else {
    clearInterval(timer);
    $('result').hidden = !lastResult;
    $('empty-state').hidden = Boolean(lastResult);
  }
}

$('product-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  showError();
  let product;
  let limits;
  try {
    product = inputMode === 'form' ? readForm() : readJSON();
    if (!product.name?.trim()) throw new Error('Укажите название товара.');
    limits = readSeoLimits();
  } catch (error) { showError(error.message); return; }
  const notice = updateDemoNotice(product);
  setBusy(true);
  try {
    const model = $('model-select').value;
    const query = model ? `?${new URLSearchParams({model})}` : '';
    const response = await fetch(`/api/generate${query}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...product, seo_limits: limits})
    });
    const result = await response.json();
    if (!response.ok) {
      const fields = result.error?.fields?.map((f) => `${f.field}: ${f.message}`).join('\n');
      throw new Error([result.error?.message || 'Ошибка генерации', fields].filter(Boolean).join('\n'));
    }
    resultNotice = notice;
    renderResult(result);
  } catch (error) {
    showError(error instanceof TypeError ? 'Соединение с приложением потеряно. Проверьте, что сервер запущен.' : error.message);
  } finally { setBusy(false); }
});

$('form-mode').addEventListener('click', () => setMode('form'));
$('json-mode').addEventListener('click', () => setMode('json'));
$('add-spec').addEventListener('click', () => { addSpec(); changed(); });
$('reset-seo').addEventListener('click', () => {
  if (seoDefaults) { fillSeoLimits(seoDefaults); changed(); }
});
$('seo-options').addEventListener('invalid', () => { $('seo-options').open = true; }, true);
$('input-fields').addEventListener('input', () => {
  changed();
  if (inputMode === 'form') updateDemoNotice({name: $('name').value});
});
$('example-select').addEventListener('change', () => {
  const example = examples.find((entry) => entry.id === $('example-select').value);
  if (!example) return;
  populateForm(example.product);
  $('json-input').value = JSON.stringify(example.product, null, 2);
  updateDemoNotice(example.product);
  changed();
  showError();
});
document.querySelectorAll('[data-view]').forEach((button) => {
  button.addEventListener('click', () => switchView(button.dataset.view));
});
$('connection').addEventListener('click', checkConnection);
$('model-select').addEventListener('change', () => {
  changed();
  $('connection-result').hidden = true;
  $('connection-dot').className = 'dot';
});
$('copy-all').addEventListener('click', () => {
  if (lastResult) copyText(outputView === 'json' ? exportedJSON() : exportedMarkdown());
});
function download(format) {
  if (!lastResult) return;
  const blob = new Blob([format === 'json' ? exportedJSON() : exportedMarkdown()],
    {type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = element('a');
  link.href = url;
  link.download = `product-card.${format === 'json' ? 'json' : 'md'}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('download-json').addEventListener('click', () => download('json'));
$('download-md').addEventListener('click', () => download('md'));

async function init() {
  const settingsRequest = loadSeoDefaults();
  addSpec('Диапазон измерений');
  addSpec('Класс точности');
  try {
    const response = await fetch('/api/examples');
    if (!response.ok) throw new Error('examples');
    examples = await response.json();
    for (const item of examples) {
      const option = element('option', '', item.label);
      option.value = item.id;
      $('example-select').append(option);
    }
  } catch { toast('Не удалось загрузить примеры. Данные можно заполнить вручную.'); }
  await settingsRequest;
}
init();
