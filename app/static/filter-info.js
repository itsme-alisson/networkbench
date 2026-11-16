(() => {
  const wrapper = document.getElementById('jinja-info');
  const button = document.getElementById('filter-info-button');
  const panel = document.getElementById('filter-info-panel');
  const content = document.getElementById('filter-info-content');
  let closeTimer;
  let loading = false;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function render(data) {
    content.replaceChildren(element('h2', 'Installed filters & modules'));
    content.append(element('p', 'Available in this NetworkBench container. Everything runs locally.', 'info-note'));
    const versions = Object.fromEntries(data.collections.map(item => [item.name, item.version]));
    for (const group of data.filter_groups) {
      const version = group.version || versions[group.name];
      content.append(element('h3', group.name + (version ? ' · ' + version : '')));
      const list = element('span', '', 'info-filters');
      for (const name of group.filters) list.append(element('code', name));
      if (!group.filters.length) list.append(element('p', 'None registered'));
      content.append(list);
    }
    content.append(element('p', 'Ansible filters accept short names and their full names, such as ansible.utils.ipaddr.', 'info-note'));
    function inventory(title, items) {
      content.append(element('h3', title));
      const list = element('span', '', 'info-packages');
      for (const item of items) list.append(element('span', item.name), element('span', item.version));
      content.append(list);
    }
    inventory('Installed Ansible collections', data.collections);
    inventory('All installed Python packages', data.packages);
    content.append(element('p', 'Installed dependencies are not automatically exposed as Jinja filters. The filter groups above show what templates can use.', 'info-note'));
  }
  async function open() {
    if (wrapper.hidden) return;
    clearTimeout(closeTimer);
    if (!panel.hidden) return;
    panel.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    // Keep the floating panel within the viewport on wide and narrow screens.
    panel.style.left = '';
    const bounds = panel.getBoundingClientRect();
    if (bounds.right > window.innerWidth - 16) panel.style.left = `${Math.min(0, window.innerWidth - 16 - bounds.right)}px`;
    if (loading) return;
    loading = true;
    content.textContent = 'Loading installed modules…';
    try {
      const response = await fetch('/api/jinja/modules', {credentials: 'same-origin'});
      if (!response.ok) throw new Error('Unable to load installed modules. Close and reopen to retry.');
      render(await response.json());
    } catch (error) {content.textContent = error.message;}
    finally {loading = false;}
  }
  function close() {
    clearTimeout(closeTimer);
    panel.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }
  wrapper.addEventListener('mouseenter', open);
  wrapper.addEventListener('mouseleave', () => {
    closeTimer = setTimeout(() => {if (!wrapper.contains(document.activeElement)) close();}, 200);
  });
  wrapper.addEventListener('focusin', open);
  wrapper.addEventListener('focusout', () => {
    closeTimer = setTimeout(() => {if (!wrapper.contains(document.activeElement)) close();}, 0);
  });
  button.addEventListener('click', open);
  document.addEventListener('pointerdown', event => {if (!wrapper.contains(event.target)) close();});
  wrapper.addEventListener('keydown', event => {
    if (event.key === 'Escape') {button.focus(); close(); event.preventDefault();}
  });
  window.NetworkBenchFilterInfo = {setVisible(visible) {close(); wrapper.hidden = !visible;}};
})();
