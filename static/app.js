const config = JSON.parse(document.getElementById('app-config').textContent);
const state = { file: null, jobId: null, events: null, issues: [], allocation: [] };

const $ = id => document.getElementById(id);
const panels = ['upload-panel', 'progress-panel', 'review-panel', 'allocation-panel', 'result-panel', 'error-panel'];

function showPanel(id) {
  panels.forEach(name => $(name).classList.toggle('hidden', name !== id));
}

function setStep(step) {
  document.querySelectorAll('.steps li').forEach(item => {
    item.classList.toggle('active', Number(item.dataset.step) <= step);
  });
}

function setFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    window.alert('Selecione um arquivo .xlsx.');
    return;
  }
  state.file = file;
  $('file-title').textContent = file.name;
  $('file-detail').textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
  $('drop-zone').classList.add('selected');
  $('start-button').disabled = false;
}

$('file-input').addEventListener('change', event => setFile(event.target.files[0]));
$('drop-zone').addEventListener('dragover', event => { event.preventDefault(); event.currentTarget.classList.add('over'); });
$('drop-zone').addEventListener('dragleave', event => event.currentTarget.classList.remove('over'));
$('drop-zone').addEventListener('drop', event => {
  event.preventDefault();
  event.currentTarget.classList.remove('over');
  setFile(event.dataTransfer.files[0]);
});

document.querySelectorAll('input[name="qtd-indv-mode"]').forEach(control => {
  control.addEventListener('change', () => {
    const custom = document.querySelector('input[name="qtd-indv-mode"]:checked').value === 'custom';
    $('qtd-indv-multiplier').disabled = !custom;
    if (custom) $('qtd-indv-multiplier').focus();
  });
});

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Falha HTTP ${response.status}`);
  return payload;
}

$('start-button').addEventListener('click', async () => {
  if (!state.file) return;
  const qtdIndvMode = document.querySelector('input[name="qtd-indv-mode"]:checked').value;
  const qtdIndvMultiplier = $('qtd-indv-multiplier').value.trim();
  if (qtdIndvMode === 'custom' && (!/^\d+$/.test(qtdIndvMultiplier) || Number(qtdIndvMultiplier) <= 0)) {
    window.alert('Informe um multiplicador inteiro positivo para QTD_INDV.');
    $('qtd-indv-multiplier').focus();
    return;
  }
  showPanel('progress-panel');
  setStep(2);
  updateProgress({ progress: 3, message: 'Enviando a planilha…', warnings: [] });
  const form = new FormData();
  form.append('file', state.file);
  form.append('qtd_indv_mode', qtdIndvMode);
  if (qtdIndvMode === 'custom') form.append('qtd_indv_multiplier', qtdIndvMultiplier);
  try {
    const job = await requestJson('/api/jobs', { method: 'POST', body: form });
    state.jobId = job.id;
    $('job-code').textContent = `Processamento ${job.id.slice(0, 8)}`;
    connectEvents();
  } catch (error) {
    showError([error.message]);
  }
});

function connectEvents() {
  if (state.events) state.events.close();
  state.events = new EventSource(`/api/jobs/${state.jobId}/events`);
  state.events.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.event === 'ping') return;
    if (message.event === 'status') handleStatus(message);
    if (message.event === 'review') loadIssues();
    if (message.event === 'allocation_review') loadAllocation();
    if (message.event === 'complete') loadJob();
  };
  state.events.onerror = () => {
    if (state.events) state.events.close();
    setTimeout(() => { if (state.jobId) connectEvents(); }, 800);
  };
}

function ensureEvents() {
  if (!state.events || state.events.readyState === EventSource.CLOSED) connectEvents();
}

async function loadJob() {
  if (!state.jobId) return;
  try { handleStatus(await requestJson(`/api/jobs/${state.jobId}`)); }
  catch (error) { showError([error.message]); }
}

function handleStatus(job) {
  updateProgress(job);
  if (job.status === 'review') loadIssues();
  if (job.status === 'allocation_review') loadAllocation();
  if (job.status === 'ready') showResult(job);
  if (job.status === 'error') showError(job.errors.length ? job.errors : [job.message]);
}

function updateProgress(job) {
  $('progress-bar').style.width = `${job.progress || 0}%`;
  $('progress-value').textContent = `${job.progress || 0}%`;
  $('status-message').textContent = job.message || 'Processando…';
  renderNotices($('warning-list'), job.warnings || []);
}

function renderNotices(container, messages) {
  container.replaceChildren();
  messages.forEach(message => {
    const item = document.createElement('div');
    item.className = 'notice';
    item.textContent = message;
    container.appendChild(item);
  });
}

async function loadIssues() {
  try {
    const payload = await requestJson(`/api/jobs/${state.jobId}/issues`);
    state.issues = payload.issues;
    renderIssues();
    showPanel('review-panel');
    setStep(3);
  } catch (error) {
    showError([error.message]);
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function field(labelText, input) {
  const wrapper = element('div', 'field');
  wrapper.append(element('label', '', labelText), input);
  return wrapper;
}

function input(type, issue, suffix = '') {
  const node = document.createElement('input');
  node.type = type;
  node.dataset.issue = issue.id;
  node.dataset.kind = issue.kind;
  if (suffix) node.dataset.part = suffix;
  return node;
}

function select(issue, choices) {
  const node = document.createElement('select');
  node.dataset.issue = issue.id;
  node.dataset.kind = issue.kind;
  node.appendChild(new Option('Selecione…', ''));
  choices.forEach(([label, value]) => node.appendChild(new Option(label, value)));
  return node;
}

function renderIssues() {
  const container = $('issues-container');
  container.replaceChildren();
  $('issue-count').textContent = `${state.issues.length} pendência(s)`;

  state.issues.forEach(issue => {
    const card = element('article', 'issue');
    card.dataset.issue = issue.id;
    const head = element('div', 'issue-head');
    const titleBox = document.createElement('div');
    titleBox.append(element('h3', '', issue.title), element('p', 'issue-message', issue.message));
    head.append(titleBox, element('span', 'row-badge', `Linhas: ${issue.rows.join(', ')}`));
    card.appendChild(head);
    if (issue.original !== null && issue.original !== undefined && issue.original !== '') {
      card.appendChild(element('div', 'original', `Original: ${issue.original}`));
    }
    card.appendChild(renderDecisionControl(issue));
    container.appendChild(card);
  });
}

function renderDecisionControl(issue) {
  if (issue.kind === 'missing_location') {
    return field('Tratamento', select(issue, [['Excluir apenas do querieData', 'exclude'], ['Manter nas saídas', 'keep']]));
  }
  if (issue.kind === 'invalid_uf') {
    return field('UF correta', select(issue, config.validUfs.map(uf => [uf, uf])));
  }
  if (issue.kind === 'municipality') {
    const wrapper = document.createElement('div');
    const code = input('text', issue);
    code.maxLength = 7;
    code.inputMode = 'numeric';
    code.placeholder = 'Código IBGE com 7 dígitos';
    wrapper.appendChild(field(`Código IBGE — UF ${issue.context.uf}`, code));
    if (issue.suggestions.length) {
      const suggestions = element('div', 'suggestions');
      issue.suggestions.forEach(suggestion => {
        const button = element('button', 'suggestion', suggestion.label);
        button.type = 'button';
        button.addEventListener('click', () => { code.value = suggestion.value; });
        suggestions.appendChild(button);
      });
      wrapper.appendChild(suggestions);
    }
    return wrapper;
  }
  if (issue.kind === 'age') {
    const row = element('div', 'field-row');
    const minimum = input('number', issue, 'min');
    const maximum = input('number', issue, 'max');
    [minimum, maximum].forEach(node => { node.min = 14; node.max = 100; });
    row.append(field('Idade mínima (opcional)', minimum), field('Idade máxima (opcional)', maximum));
    return row;
  }
  if (issue.kind === 'education') {
    return checkboxGroup(issue, config.educationLevels, config.educationDescriptions, true);
  }
  if (issue.kind === 'sex') {
    return checkboxGroup(issue, Object.keys(config.sexDescriptions), config.sexDescriptions, false);
  }
  if (issue.kind === 'quantity') {
    const quantity = input('number', issue);
    quantity.min = 0;
    quantity.step = 1;
    return field('Quantidade correta', quantity);
  }
  return element('p', 'issue-message', 'Tipo de pendência não suportado pela interface.');
}

function checkboxGroup(issue, options, descriptions, multiple) {
  const wrapper = element('div', 'field');
  wrapper.appendChild(element('span', 'field-label', multiple ? 'Níveis aceitos' : 'Regra aplicável'));
  const grid = element('div', 'check-grid');
  options.forEach(option => {
    const label = element('label', 'check-choice');
    const control = document.createElement('input');
    control.type = multiple ? 'checkbox' : 'radio';
    control.name = `choice-${issue.id}`;
    control.value = option;
    control.dataset.issue = issue.id;
    control.dataset.kind = issue.kind;
    label.append(control, document.createTextNode(descriptions[option] || option));
    grid.appendChild(label);
  });
  wrapper.appendChild(grid);
  return wrapper;
}

$('review-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const values = collectDecisions();
    showPanel('progress-panel');
    setStep(2);
    updateProgress({ progress: 75, message: 'Aplicando decisões…', warnings: [] });
    await requestJson(`/api/jobs/${state.jobId}/decisions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values, remember: $('remember-decisions').checked }),
    });
    ensureEvents();
  } catch (error) {
    window.alert(error.message);
  }
});

function collectDecisions() {
  const values = {};
  for (const issue of state.issues) {
    const controls = [...document.querySelectorAll(`[data-issue="${issue.id}"]`)].filter(node => node.matches('input, select'));
    let value;
    if (issue.kind === 'education' || issue.kind === 'sex') {
      value = controls.filter(node => node.checked).map(node => node.value);
      if (!value.length) throw new Error(`Preencha: ${issue.title}.`);
    } else if (issue.kind === 'age') {
      const minimum = controls.find(node => node.dataset.part === 'min').value;
      const maximum = controls.find(node => node.dataset.part === 'max').value;
      if (!minimum && !maximum) throw new Error(`Informe ao menos um limite para: ${issue.original}.`);
      value = { min: minimum ? Number(minimum) : null, max: maximum ? Number(maximum) : null };
      if (value.min !== null && value.max !== null && value.min > value.max) throw new Error('A idade mínima não pode superar a máxima.');
    } else {
      value = controls[0]?.value.trim();
      if (!value) throw new Error(`Preencha: ${issue.title}.`);
      if (issue.kind === 'quantity') value = Number(value);
      if (issue.kind === 'municipality' && !/^\d{7}$/.test(value)) throw new Error('O código IBGE deve possuir sete dígitos.');
    }
    values[issue.id] = value;
  }
  return values;
}

async function loadAllocation() {
  try {
    const payload = await requestJson(`/api/jobs/${state.jobId}/allocation`);
    state.allocation = payload.rows;
    renderAllocation(payload);
    showPanel('allocation-panel');
    setStep(4);
  } catch (error) {
    showError([error.message]);
  }
}

function renderAllocation(payload) {
  $('localities-count').textContent = Number(payload.localities).toLocaleString('pt-BR');
  const body = $('allocation-body');
  body.replaceChildren();
  payload.rows.forEach(item => {
    const row = document.createElement('tr');
    const locality = document.createElement('td');
    locality.append(
      element('strong', '', item.cidade || 'Município não informado'),
      element('small', '', item.uf || 'UF não informada'),
    );
    const code = element('td', 'code-cell', item.cod_ibge);
    const vacancies = element('td', 'number-cell', Number(item.quantidade_vagas).toLocaleString('pt-BR'));
    const valueCell = document.createElement('td');
    const value = document.createElement('input');
    value.className = 'allocation-input';
    value.type = 'number';
    value.min = '0';
    value.step = '1';
    value.value = item.qtd_indv;
    value.dataset.code = item.cod_ibge;
    value.addEventListener('input', updateAllocationTotal);
    valueCell.appendChild(value);
    row.append(locality, code, vacancies, valueCell);
    body.appendChild(row);
  });
  updateAllocationTotal();
}

function updateAllocationTotal() {
  const total = [...document.querySelectorAll('.allocation-input')].reduce((sum, input) => {
    const value = Number(input.value);
    return sum + (Number.isInteger(value) && value >= 0 ? value : 0);
  }, 0);
  $('qtd-indv-total').textContent = total.toLocaleString('pt-BR');
}

$('allocation-form').addEventListener('submit', async event => {
  event.preventDefault();
  const values = {};
  for (const input of document.querySelectorAll('.allocation-input')) {
    const value = Number(input.value);
    if (!Number.isInteger(value) || value < 0) {
      window.alert(`Informe um QTD_INDV inteiro e não negativo para ${input.dataset.code}.`);
      input.focus();
      return;
    }
    values[input.dataset.code] = value;
  }
  try {
    showPanel('progress-panel');
    setStep(4);
    updateProgress({ progress: 90, message: 'Gerando arquivos com os valores confirmados…', warnings: [] });
    await requestJson(`/api/jobs/${state.jobId}/allocation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    });
    ensureEvents();
  } catch (error) {
    showError([error.message]);
  }
});

function showResult(job) {
  if (state.events) state.events.close();
  showPanel('result-panel');
  setStep(5);
  $('result-summary').textContent = `${job.rows} vaga(s) processada(s). Revise o relatório junto aos arquivos gerados.`;
  renderNotices($('result-warnings'), job.warnings || []);
  document.querySelectorAll('[data-kind]').forEach(link => {
    link.href = `/api/jobs/${state.jobId}/download/${link.dataset.kind}`;
  });
}

function showError(errors) {
  if (state.events) state.events.close();
  showPanel('error-panel');
  const container = $('error-list');
  container.replaceChildren();
  errors.forEach(message => container.appendChild(element('div', '', message)));
}

$('new-button').addEventListener('click', () => window.location.reload());
$('retry-button').addEventListener('click', () => window.location.reload());
