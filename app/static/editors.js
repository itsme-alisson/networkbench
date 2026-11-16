/* All editor code and language modes are bundled locally. */
(() => {
  const editors = {};
  let tool = 'jinja';
  let direction = 'json_to_yaml';
  function inputMode(value) {
    if (tool === 'json_yaml' && direction === 'yaml_to_json') return 'yaml';
    if (tool === 'json' || tool === 'json_yaml') return {name: 'javascript', json: true};
    if (tool === 'jinja') return /^[\s]*[\[{]/.test(value) ? {name: 'javascript', json: true} : 'yaml';
    return tool === 'xpath' ? 'xml' : 'text/plain';
  }
  function updateInputMode() {
    const mode = inputMode(editors.input.getValue());
    if (JSON.stringify(editors.input.getOption('mode')) !== JSON.stringify(mode)) editors.input.setOption('mode', mode);
  }
  for (const id of ['input', 'template', 'result']) {
    const field = document.getElementById(id);
    const editor = CodeMirror.fromTextArea(field, {
      mode: 'text/plain', theme: 'networkbench', lineNumbers: true,
      readOnly: id === 'result', matchBrackets: true,
      indentUnit: 2, tabSize: 2, lineWrapping: false,
      inputStyle: 'contenteditable', spellcheck: false,
      viewportMargin: 10,
      extraKeys: id === 'result' ? {Tab: false, 'Shift-Tab': false} : {
        Tab(cm) {
          if (cm.somethingSelected()) cm.indentSelection('add');
          else cm.replaceSelection('  ', 'end', '+input');
        },
        'Shift-Tab'(cm) { cm.indentSelection('subtract'); },
        Esc(cm) { cm.getInputField().blur(); }
      },
      placeholder: field.placeholder
    });
    editors[id] = editor;
    editor.getInputField().setAttribute('aria-label', id === 'input' ? 'Data editor' : id === 'template' ? 'Template editor' : 'Result editor');
    if (id !== 'result') {
      editor.on('beforeChange', (cm, change) => {
        const size = cm.getValue().length - (cm.indexFromPos(change.to) - cm.indexFromPos(change.from)) + change.text.join('\n').length;
        if (size > 100000) {
          change.cancel();
          document.getElementById('notice').textContent = 'Editor limit: 100,000 characters. Paste a smaller input.';
        }
      });
      editor.on('change', (cm, change) => {
        cm.save();
        if (id === 'input') updateInputMode();
        if (change.origin !== 'setValue') field.dispatchEvent(new Event('input', {bubbles: true}));
      });
    }
    document.querySelector(`label[for="${id}"]`).addEventListener('click', () => editor.focus());
  }
  window.NetworkBenchEditors = {
    setValue(id, value, resetHistory = false) {
      const editor = editors[id];
      if (editor.getValue() !== value) editor.setValue(value);
      // Keep original fields synchronized for API requests and clipboard actions.
      document.getElementById(id).value = value;
      if (resetHistory || id === 'result') editor.clearHistory();
    },
    setTool(value, conversionDirection = 'json_to_yaml') {
      tool = value;
      direction = conversionDirection;
      updateInputMode();
      editors.template.setOption('mode', tool === 'jinja' ? 'jinja2' : tool === 'ttp' ? 'xml' : 'text/plain');
      editors.result.setOption('mode', tool === 'jinja' ? 'text/plain' : tool === 'json_yaml' && direction === 'json_to_yaml' ? 'yaml' : {name: 'javascript', json: true});
      editors.input.setOption('placeholder', tool === 'json' ? 'Paste JSON to validate…' : 'Paste your input data…');
      editors.template.getInputField().setAttribute('aria-label', tool === 'xpath' ? 'XPath expression editor' : 'Template editor');
      requestAnimationFrame(() => Object.values(editors).forEach(editor => editor.refresh()));
    }
  };
})();
