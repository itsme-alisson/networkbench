const $ = selector => document.querySelector(selector);
const tools = {
  json_yaml: {title: 'JSON ↔ YAML', description: 'Convert JSON and YAML locally. Choose the direction and paste your data.', type: 'CONVERTER', input: 'JSON'},
  json: {title: 'JSON validator', description: 'Validate JSON syntax and format it. Duplicate keys are flagged; no schema is required.', type: 'JSON', input: 'JSON'},
  jinja: {title: 'Jinja renderer', description: 'Render JSON/YAML variables. Network filters: ipaddr, ipv4, ipv6, ipmath, ipsubnet and more.', type: 'JINJA2', input: 'JSON / YAML'},
  ttp: {title: 'TTP parser', description: 'Extract structured data from CLI output using a TTP template.', type: 'TTP', input: 'CLI OUTPUT'},
  textfsm: {title: 'TextFSM parser', description: 'Parse command output into JSON records.', type: 'TEXTFSM', input: 'CLI OUTPUT'},
  xpath: {title: 'XPath validator', description: 'Evaluate XPath against XML. Use default: for a default XML namespace.', type: 'XPATH', input: 'XML'}
};
let examples = {};
function populateExamples() {
  const select = $('#examples');
  select.replaceChildren(new Option('Examples', ''));
  for (const [index, example] of (examples[active.tool] || []).entries()) {
    select.add(new Option(example.name, String(index)));
  }
  select.disabled = !(examples[active.tool]?.length);
}
async function loadExamples() {
  try {
    examples = await api('/static/examples.json');
    populateExamples();
  } catch (error) { $('#notice').textContent = 'Could not load examples. Refresh the page to retry. ' + error.message; }
}

const states = {};
const pendingStates = new Set();
let active;
let cleaning = false;
let renderTimer;
let renderController;
let renderSequence = 0;
let savedItems = [];
function blank(tool) {return {tool, name: '', template: '', input: '', options: {}, result: '', version: 0, savedVersion: 0, saving: false};}
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check your input and try again.');
  return body;
}
function status(text, error = false) {$('#render-status').textContent = text; $('#render-status').classList.toggle('error', error);}
function scheduleRender() {
  clearTimeout(renderTimer);
  renderController?.abort();
  const sequence = ++renderSequence;
  const state = active;
  NetworkBenchEditors.setValue('result', '');
  state.result = '';
  if (['json', 'json_yaml'].includes(state.tool) ? !state.input.trim() : !state.template.trim()) {status(['json', 'json_yaml'].includes(state.tool) ? 'Enter data to begin.' : 'Enter a template to begin.'); return;}
  status('Waiting for edits…');
  renderTimer = setTimeout(async () => {
    renderController = new AbortController();
    status('Processing locally…');
    try {
      const result = await api('/api/execute', {method: 'POST', signal: renderController.signal, body: JSON.stringify({tool: state.tool, template: state.template, input: state.input, conversion_direction: state.options.direction || 'json_to_yaml'})});
      if (sequence !== renderSequence) return;
      state.result = result.result;
      NetworkBenchEditors.setValue('result', result.result);
      status(state.tool === 'json_yaml' ? 'Converted locally' : state.tool === 'json' ? 'Valid JSON' : result.result ? 'Updated' : 'Updated · empty result');
    } catch (error) {
      if (sequence !== renderSequence || error.name === 'AbortError') return;
      status(error.message, true);
    }
  }, 450);
}
function saveStatus(state, text) {if (state === active) $('#save-status').textContent = text;}
function save(state) {
  if (cleaning || state.deleting) return Promise.resolve();
  if (state.savePromise) return state.savePromise;
  state.savePromise = saveWorkspace(state).finally(() => {
    state.savePromise = null;
    if (state.saveSucceeded && !state.deleting && state.version !== state.savedVersion && !state.conflict) save(state);
  });
  return state.savePromise;
}
async function saveWorkspace(state) {
  clearTimeout(state.timer);
  if (state.saving || state.version === state.savedVersion || state.conflict) return;
  state.saving = true;
  const version = state.version;
  saveStatus(state, 'Saving…');
  const payload = {tool: state.tool, name: state.name.trim() || `Untitled ${tools[state.tool].title}`, template: state.template, input: state.input, options: state.options, result: ''};
  if (state.id) payload.revision = state.revision;
  state.saveSucceeded = false;
  try {
    const item = await api(state.id ? `/api/workspaces/${state.id}` : '/api/workspaces', {method: state.id ? 'PUT' : 'POST', body: JSON.stringify(payload)});
    state.id = item.id; state.revision = item.revision; state.savedVersion = version;
    state.saveSucceeded = true;
    if (state.version === state.savedVersion) pendingStates.delete(state);
    saveStatus(state, 'Saved locally');
    await refreshSaved();
  } catch (error) {
    saveStatus(state, 'Not saved — ' + error.message);
    $('#notice').textContent = 'Your edits remain in this tab. Edit again to retry; for a revision conflict, reopen the saved workspace first.';
    state.conflict = error.message.includes('Workspace changed');
  } finally {
    state.saving = false;

  }
}
function changed() {
  if (cleaning) return;
  active.name = $('#name').value;
  active.template = $('#template').value;
  active.input = $('#input').value;
  active.version++;
  pendingStates.add(active);
  saveStatus(active, 'Unsaved changes');
  clearTimeout(active.timer);
  const state = active;
  state.timer = setTimeout(() => save(state), 900);
}
function display(state) {
  if (!active || active.tool !== state.tool) $('#saved-search').value = '';
  active = state; states[state.tool] = state;
  const tool = tools[state.tool];
  NetworkBenchFilterInfo.setVisible(state.tool === 'jinja');
  $('#template-pane').hidden = ['json', 'json_yaml'].includes(state.tool);
  $('#conversion-direction').hidden = state.tool !== 'json_yaml';
  $('#conversion-direction').value = state.options.direction || 'json_to_yaml';
  $('.workspace').classList.toggle('json-workspace', ['json', 'json_yaml'].includes(state.tool));
  $('#input').placeholder = state.tool === 'json' ? 'Paste JSON to validate…' : 'Paste your input data…';
  document.querySelectorAll('[data-tool]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tool === state.tool)));
  $('#title').textContent = tool.title; $('#description').textContent = tool.description;
  $('#template-label').textContent = state.tool === 'xpath' ? 'XPath expression' : 'Template';
  $('#template-type').textContent = tool.type;
  $('#input-type').textContent = state.tool === 'json_yaml' ? (state.options.direction === 'yaml_to_json' ? 'YAML' : 'JSON') : tool.input;
  document.querySelector('label[for="result"]').textContent = state.tool === 'json_yaml' ? (state.options.direction === 'yaml_to_json' ? 'JSON result' : 'YAML result') : 'Result';
  $('#name').value = state.name;
  NetworkBenchEditors.setValue('template', state.template, true);
  NetworkBenchEditors.setValue('input', state.input, true);
  NetworkBenchEditors.setTool(state.tool, state.options.direction || 'json_to_yaml');
  $('#save-status').textContent = state.version !== state.savedVersion ? 'Unsaved changes' : state.id ? 'Saved locally' : state.examplePreview ? 'Example preview · edit to save locally' : 'Changes are saved automatically';
  $('#notice').textContent = ''; populateExamples(); populateSaved(); scheduleRender();
}
function toolWorkspaces() {
  return savedItems.filter(item => item.tool === active.tool)
    .sort((a, b) => b.updated_at - a.updated_at || a.id.localeCompare(b.id));
}
function populateSaved() {
  $('#delete-workspace').disabled = !active.id || !!active.deleting;
  const items = toolWorkspaces();
  const query = $('#saved-search').value.trim().toLocaleLowerCase();
  const matches = items.filter(item => item.name.toLocaleLowerCase().includes(query));
  $('#saved-count').textContent = String(items.length);
  $('#open-latest').disabled = !items.length || cleaning;
  $('#saved').replaceChildren(new Option(!items.length ? 'No saved work for this tool yet' : !matches.length ? 'No matching workspaces' : 'Choose previous work…', ''));
  for (const item of matches) {
    const date = new Date(item.updated_at * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
    $('#saved').add(new Option(`${item.name} — ${date}`, item.id));
  }
  $('#saved').disabled = !matches.length || cleaning;
  $('#saved').value = matches.some(item => item.id === active.id) ? active.id : '';
}

async function refreshSaved() {
  try {
    const items = [];
    for (let offset = 0; ; offset += 100) {
      const batch = await api(`/api/workspaces?limit=100&offset=${offset}`);
      items.push(...batch);
      if (batch.length < 100) break;
    }
    savedItems = items; populateSaved();
  } catch (error) {$('#notice').textContent = error.message;}
}
for (const button of document.querySelectorAll('[data-tool]')) button.addEventListener('click', () => {
  save(active); display(states[button.dataset.tool] || blank(button.dataset.tool));
});
for (const id of ['template', 'input']) $(`#${id}`).addEventListener('input', () => {changed(); scheduleRender();});
$('#name').addEventListener('input', changed);
function updateConversionDirection() {
  active.options.direction = $('#conversion-direction').value;
  $('#input-type').textContent = active.options.direction === 'yaml_to_json' ? 'YAML' : 'JSON';
  document.querySelector('label[for="result"]').textContent = active.options.direction === 'yaml_to_json' ? 'JSON result' : 'YAML result';
  NetworkBenchEditors.setTool(active.tool, active.options.direction);
}
$('#conversion-direction').addEventListener('change', () => {
  updateConversionDirection(); changed(); scheduleRender();
});

$('#examples').addEventListener('change', () => {
  const selected = $('#examples').value;
  $('#examples').value = '';
  if (selected === '') return;
  const example = examples[active.tool]?.[Number(selected)];
  if (!example) return;
  // Preserve edits to the previous workspace; examples start as unsaved previews.
  save(active);
  display({...blank(active.tool), name: example.name, template: example.template,
    input: example.data, options: {...(example.options || {})}, examplePreview: true});
});
$('#clear').addEventListener('click', () => {
  if ((active.template || active.input) && !confirm('Clear this workspace’s template and data? This change will be saved.')) return;
  NetworkBenchEditors.setValue('template', ''); NetworkBenchEditors.setValue('input', ''); changed(); scheduleRender();
});
$('#new').addEventListener('click', () => {save(active); display(blank(active.tool));});
let openingWorkspace = false;
async function openWorkspace(id) {
  if (!id || cleaning || openingWorkspace) return;
  const previous = active;
  openingWorkspace = true;
  try {
    // Keep edits before opening another saved workspace.
    await save(previous);
    if (previous.version !== previous.savedVersion) {
      $('#notice').textContent = 'Could not save your current edits. Resolve the save error before opening other work.';
      populateSaved();
      return;
    }
    const version = previous.version;
    const item = await api(`/api/workspaces/${id}`);
    if (active !== previous || cleaning) return;
    if (previous.version !== version) {
      $('#notice').textContent = 'You made more edits while loading. Open the saved workspace again when ready.';
      populateSaved();
      return;
    }
    pendingStates.delete(previous);
    display({...item, version: 0, savedVersion: 0, saving: false});
  } catch (error) {$('#notice').textContent = error.message; populateSaved();}
  finally {openingWorkspace = false;}
}
$('#saved').addEventListener('change', () => openWorkspace($('#saved').value));
$('#saved-search').addEventListener('input', populateSaved);
$('#open-latest').addEventListener('click', () => openWorkspace(toolWorkspaces()[0]?.id));
$('#reload').addEventListener('click', refreshSaved);
$('#delete-workspace').addEventListener('click', async () => {
  const state = active;
  if (!state.id || state.deleting) return;
  if (!confirm(`Permanently delete "${state.name || 'Untitled workspace'}" and its local files?`)) return;
  state.deleting = true;
  clearTimeout(state.timer);
  populateSaved();
  try {
    // Wait for any in-flight autosave before deletion so it cannot recreate files.
    if (state.savePromise) await state.savePromise;
    await api(`/api/workspaces/${state.id}`, {method: 'DELETE'});
    pendingStates.delete(state);
    const replacement = blank(state.tool);
    if (states[state.tool] === state) states[state.tool] = replacement;
    if (active === state) display(replacement);
    await refreshSaved();
    $('#notice').textContent = 'Saved workspace and its local files deleted.';
  } catch (error) {
    state.deleting = false;
    $('#notice').textContent = 'Could not delete workspace: ' + error.message;
    populateSaved();
  }
});

$('#cleanup-workspaces').addEventListener('click', async () => {
  if (cleaning || !confirm('Permanently delete ALL saved workspaces for ALL tools, including every file and folder inside workspaces? Current drafts will also be cleared. This cannot be undone.')) return;
  cleaning = true;
  document.body.inert = true;
  const affected = new Set([...Object.values(states), ...pendingStates, active]);
  for (const state of affected) {
    state.deleting = true;
    clearTimeout(state.timer);
  }
  clearTimeout(renderTimer);
  renderController?.abort();
  ++renderSequence;
  $('#notice').textContent = 'Deleting all saved workspaces…';
  try {
    // Drain autosaves, including POSTs for new drafts, before removing files.
    await Promise.all([...affected].map(state => state.savePromise));
    await api('/api/workspaces', {method: 'DELETE'});
    pendingStates.clear();
    for (const tool of Object.keys(states)) delete states[tool];
    savedItems = [];
    display(blank(active.tool));
    $('#notice').textContent = 'All saved workspaces and their files deleted.';
  } catch (error) {
    for (const state of affected) state.deleting = false;
    await refreshSaved();
    $('#notice').textContent = 'Could not finish cleanup: ' + error.message;
  } finally {
    cleaning = false;
    document.body.inert = false;
    populateSaved();
  }
});

$('#copy').addEventListener('click', async () => {
  try {await navigator.clipboard.writeText($('#result').value); $('#copy').textContent = 'Copied'; setTimeout(() => {$('#copy').textContent = 'Copy';}, 1500);}
  catch {status('Select the result and copy it manually. Clipboard access is unavailable.', true);}
});
window.addEventListener('beforeunload', event => {
  if (pendingStates.size > 0) {event.preventDefault(); event.returnValue = '';}
});
display(blank('jinja'));
refreshSaved();

loadExamples();
