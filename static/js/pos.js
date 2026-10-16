/* Native DOM presentation only. All prices, tax, discounts and stock remain server-owned. */
(() => {
    // Keep transient feedback in the workstation flow, away from payment controls.
    const feedback = document.getElementById('pos-feedback');
    const notice = document.getElementById('operation-notice');
    if (feedback && notice) feedback.append(notice);

    document.addEventListener('keydown', event => {
        if (event.key === 'F2') { event.preventDefault(); document.getElementById('sale-search-input')?.focus(); }
        if (event.altKey && event.key.toLowerCase() === 'c') { event.preventDefault(); openCart(); }
    });
    function openCart() {
        const cart = document.getElementById('pos-cart');
        const drawer = cart?.querySelector('details');
        if (drawer) drawer.open = true;
        cart?.scrollIntoView({block: 'nearest'}); cart?.focus();
    }
    document.addEventListener('click', event => {
        if (event.target.closest('[data-pos-search]')) document.getElementById('sale-search-input')?.focus();
        if (event.target.closest('[data-open-cart]')) { event.preventDefault(); openCart(); }
    });
    const form = document.querySelector('[data-pos-designer]');
    if (!form) return;
    const input = document.getElementById('layout-configuration');
    const config = JSON.parse(input.value);
    const products = document.getElementById('designer-products');
    const categories = document.getElementById('designer-categories');
    const favorites = new Set(config.favorites);
    let dirty = false;
    let dragged = null;
    const announcement = message => window.TCCPUI?.announce(message);
    const listIds = list => Array.from(list.children).map(node => Number(node.dataset.id));
    function serialize() {
        config.product_order = listIds(products); config.category_order = listIds(categories);
        config.favorites = Array.from(favorites);
        config.colors = {};
        products.querySelectorAll('li').forEach(row => config.colors[row.dataset.id] = row.querySelector('[data-color]').value);
        config.preset = document.getElementById('layout-preset').value;
        config.density = document.getElementById('layout-density').value;
        config.show_images = document.getElementById('layout-images').checked;
        config.show_categories = document.getElementById('layout-categories').checked;
        input.value = JSON.stringify(config);
        const preview = document.getElementById('pos-layout-preview');
        preview.className = 'pos-preview-grid density-' + (config.preset === 'quick' ? 'large' : config.density);
        preview.querySelectorAll('.product-initial').forEach(node => node.hidden = !config.show_images);
        const categoryPreview = document.getElementById('pos-preview-categories');
        if (categoryPreview) {
            categoryPreview.hidden = !config.show_categories;
            if (categories.children.length) {
                categoryPreview.replaceChildren();
                Array.from(categories.children).forEach(row => { const label = document.createElement('span'); label.textContent = row.dataset.name; categoryPreview.append(label); });
            }
        }
        // Preview never contains a sale form or a mutation endpoint.
        if (products.children.length) {
            preview.replaceChildren();
            Array.from(products.children).slice(0, 8).forEach(row => {
                const tile = document.createElement('article');
                tile.className = 'pos-preview-tile color-' + row.querySelector('[data-color]').value;
                if (config.show_images) { const image = document.createElement('span'); image.className = 'product-initial'; const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'pos-icon'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.7'); const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', 'M4 7l8-4 8 4v10l-8 4-8-4z M4 7l8 4 8-4 M12 11v10'); svg.append(path); image.append(svg); image.setAttribute('aria-hidden', 'true'); tile.append(image); }
                const name = document.createElement('strong'); name.textContent = row.dataset.name; tile.append(name); preview.append(tile);
            });
        }
    }
    function changed(message = 'Layout preview updated. Save to share these settings.') { dirty = true; serialize(); announcement(message); }
    function button(text, attribute, value, label) {
        const node = document.createElement('button'); node.type = 'button'; node.className = 'small-button'; node.textContent = text; node.setAttribute(attribute, value); if (label) node.setAttribute('aria-label', label); return node;
    }
    function addRow(list, id, name, isProduct) {
        if (listIds(list).includes(id)) { announcement('This shortcut is already pinned.'); return; }
        if (list.children.length >= (isProduct ? 100 : 50)) { announcement('Shortcut limit reached. Unpin an item first.'); return; }
        const row = document.createElement('li'); row.draggable = true; row.dataset.id = String(id); row.dataset.name = name;
        const title = document.createElement('strong'); title.textContent = name; row.append(title);
        if (isProduct) {
            const label = document.createElement('label'); label.textContent = 'Tile color ';
            const select = document.createElement('select'); select.setAttribute('data-color', '');
            ['blue', 'navy', 'green', 'amber', 'purple'].forEach(color => { const option = document.createElement('option'); option.value = color; option.textContent = color; select.append(option); }); label.append(select); row.append(label);
            const favoriteLabel = document.createElement('label'); const favorite = document.createElement('input'); favorite.type = 'checkbox'; favorite.setAttribute('data-favorite', ''); favorite.checked = favorites.has(id); favoriteLabel.append(favorite, ' Favorite'); row.append(favoriteLabel);
        }
        row.append(button('↑', 'data-move', 'up', 'Move ' + name + ' up'), button('↓', 'data-move', 'down', 'Move ' + name + ' down'), button('Unpin', 'data-unpin', '', 'Unpin ' + name)); list.append(row); changed();
    }
    form.addEventListener('click', event => {
        const pin = event.target.closest('[data-pin]');
        if (pin) { addRow(products, Number(pin.dataset.pin), pin.dataset.name, true); return; }
        if (event.target.closest('[data-pin-category]')) { const select = document.getElementById('designer-category'); if (select.value) addRow(categories, Number(select.value), select.selectedOptions[0].textContent, false); return; }
        const row = event.target.closest('[data-sort-list] li');
        if (!row) return;
        const move = event.target.closest('[data-move]');
        if (move) {
            const sibling = move.dataset.move === 'up' ? row.previousElementSibling : row.nextElementSibling;
            if (sibling) { if (move.dataset.move === 'up') row.parentElement.insertBefore(row, sibling); else row.parentElement.insertBefore(sibling, row); changed('Shortcut moved. Save to keep the order.'); move.focus(); }
        }
        if (event.target.closest('[data-unpin]')) { row.remove(); changed('Shortcut unpinned. Favorites remain available separately.'); }
    });
    form.addEventListener('change', event => {
        const target = event.target;
        if (target.matches('[data-favorite],[data-catalog-favorite]')) {
            const id = Number(target.dataset.catalogFavorite || target.closest('li').dataset.id);
            if (target.checked) favorites.add(id); else favorites.delete(id);
            form.querySelectorAll('[data-favorite],[data-catalog-favorite]').forEach(other => { const otherId = Number(other.dataset.catalogFavorite || other.closest('li').dataset.id); if (otherId === id) other.checked = target.checked; });
        }
        changed();
    });
    form.querySelectorAll('[data-sort-list]').forEach(list => {
        list.addEventListener('dragstart', event => { dragged = event.target.closest('li'); if (dragged?.parentElement !== list) return; dragged.classList.add('dragging'); event.dataTransfer.setData('text/plain', dragged.dataset.id); event.dataTransfer.effectAllowed = 'move'; });
        list.addEventListener('dragover', event => { if (dragged?.parentElement === list) { event.preventDefault(); list.classList.add('drag-target'); } });
        list.addEventListener('drop', event => { if (dragged?.parentElement !== list) return; event.preventDefault(); const target = event.target.closest('li'); if (target && target !== dragged) list.insertBefore(dragged, target); else if (!target) list.append(dragged); list.classList.remove('drag-target'); changed('Shortcut moved. Save to keep the order.'); });
        list.addEventListener('dragend', () => { dragged?.classList.remove('dragging'); list.classList.remove('drag-target'); dragged = null; });
    });
    form.addEventListener('submit', event => { serialize(); queueMicrotask(() => { if (!event.defaultPrevented) dirty = false; }); });
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    serialize();
})();
