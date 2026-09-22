/*
 * xadmin.widget.select.js -- the one place where a <select> becomes a selectize control, in
 * the admin and on the site (#7616).
 *
 * Every form that goes through `.exform` passes through here. The scope decides the rule:
 *
 *   inside form.exform (the admin templates declare it): every <select> is initialized
 *       .select-search     fk-ajax single select, options come from the changelist JSON
 *       .dependent-select  options follow another field of the form (#7612)
 *       select[multiple]   M2M widgets and multi-value filters (chips with a remove button)
 *       select             every other single select
 *   anywhere else (site, Hidra widgets): only select.selectize is initialized, with the
 *       semantics the site always had -- allowEmptyOption unless .selectize-dont-allow-empty,
 *       remove_button + delimiter + persist:false on multiples, named Nunjucks templates,
 *       hideInput on a filled single, destroy + reinit when the form renders again
 *
 * A widget or template tunes its control by attribute, never by shipping a script of its own:
 *
 *   data-search-url + data-choices   remote options (fk-ajax, the `_q_`/`_cols` contract of the changelist)
 *   data-field + data-dependent-parent   (dependent-select) child name and parent name; the options
 *                                    come from data-search-url as {items: [{id, name}], initial}
 *   data-dependent-hide-empty        (dependent-select) hide the .form-group while there are no items
 *   data-dependent-empty-label       (dependent-select) labelled empty option on top ("All cities")
 *   data-selectize-plugins           extra vendor plugins, space or comma separated ("drag_drop")
 *   data-selectize-min-length        read by the state_messages plugin (characters before searching)
 *   data-selectize-nunjucks-render-item / -option   precompiled .njk templates (window.nunjucks); the
 *                                    <option> carries the template data as JSON in data-data
 *   data-selectize-class-container-loading / -loading-control / -error   classes of the hidra_* states
 *   data-placeholder                 read by the vendor itself
 *   class selectize-off              leave the native <select> alone
 *   class select-placeholder         (admin) render the chosen item as "Label: text"
 *   class select-preload             (admin fk-ajax) load on mount instead of on the first focus
 *
 * Every instance gets the site helpers other scripts call: hidra_add_loading(),
 * hidra_remove_loading([triggerUpdate]), hidra_show_error([message]) and hidra_clear_options()
 * (the double clearOptions that also drops the selected option).
 *
 * Right before the vendor runs, `selectize_pre_init` fires on the <select> with the settings
 * object, so a page script can still adjust them without a fifth branch here.
 */
;(function ($) {
    'use strict';

    if (!$.fn.exform) {
        // no form pipeline on this page: nothing to register into
        return;
    }

    // Columns the changelist JSON returns for a remote search: {objects: [{id, __str__}]}.
    var SEARCH_COLS = 'id.__str__';

    function getPlaceholder($el) {
        return $el.hasClass('select-placeholder') && $el.data('placeholder') ? $el.data('placeholder') : null;
    }

    // `this` is the selectize instance: the vendor applies render templates on it.
    function selectPlaceholderTemplate(data, escape) {
        var label = escape(getPlaceholder(this.$input)),
            text = escape(data[this.settings.labelField]);
        return $.fn.nunjucks_env.renderString(
            '<div class="selectize-field-content" title="{{text}}">' +
            '<span class="selectize-field-label">{{label}}:</span>' +
            '<span class="selectize-field-text">{{text}}</span>' +
            '<div>',
            {label: label, text: text});
    }

    /**
     * Render functions for the precompiled Nunjucks templates a widget names in
     * data-selectize-nunjucks-render-item / -option, or null when it names none. The template
     * sees the <option>'s data-data JSON as `item`; an option without data (the empty
     * "--------") falls back to the vendor's plain render, because render must return HTML.
     */
    function nunjucksRenderers($select) {
        var itemTemplate = $select.attr('data-selectize-nunjucks-render-item'),
            optionTemplate = $select.attr('data-selectize-nunjucks-render-option'),
            render = {};
        if (!window.nunjucks || !(itemTemplate || optionTemplate)) {
            return null;
        }
        function renderer(templateName, className) {
            return function (data, escape) {
                if (!data.data || $.isEmptyObject(data.data)) {
                    // `text` is the vendor's labelField for a select read from the HTML, which is where
                    // every widget naming a template lives
                    return '<div class="' + className + '">' + escape(data.text) + '</div>';
                }
                return window.nunjucks.render(templateName, {item: data.data, escape: escape});
            };
        }
        if (itemTemplate) {
            render.item = renderer(itemTemplate, 'item');
        }
        if (optionTemplate) {
            render.option = renderer(optionTemplate, 'option');
        }
        return render;
    }

    /**
     * Remote loader for a select carrying `data-search-url` (fk-ajax), or null when the
     * options are already in the HTML. The URL is the changelist of the related model;
     * `data-choices` appends the filters the field is restricted to.
     */
    function remoteLoader($el) {
        var searchUrl = $el.attr('data-search-url');
        if (!searchUrl) {
            return null;
        }
        return function (query, callback) {
            $.ajax({
                url: searchUrl + ($el.attr('data-choices') || ''),
                dataType: 'json',
                type: 'GET',
                data: {'_q_': query, '_cols': SEARCH_COLS},
                error: function () {
                    callback();
                },
                success: function (res) {
                    // On a change form the object being edited must not offer itself (self FK).
                    var objectId = window.xadmin && window.xadmin.object_id,
                        objects = res.objects || [];
                    if (objectId) {
                        objects = $.grep(objects, function (item) {
                            return item.id !== objectId;
                        });
                    }
                    callback(objects);
                }
            });
        };
    }

    /**
     * The branch defaults plus whatever the widget asked for in `data-selectize-plugins`.
     * Duplicates are dropped: the vendor loads a plugin once per name anyway, but a clean list
     * keeps the order readable when debugging.
     */
    function pluginsFor($el, base) {
        var plugins = base.slice(),
            extra = ($el.attr('data-selectize-plugins') || '').trim();
        if (extra) {
            $.each(extra.split(/[\s,]+/), function (idx, name) {
                if (name && $.inArray(name, plugins) === -1) {
                    plugins.push(name);
                }
            });
        }
        return plugins;
    }

    /**
     * The site helpers, on every instance: other site scripts (and the dependent branch) call
     * them. The state classes are configurable per widget by data attribute.
     */
    function attachHelpers($select, instance) {
        var loadingClass = $select.attr('data-selectize-class-container-loading') || 'selectize-loading',
            loadingControlClass = $select.attr('data-selectize-class-container-loading-control') || 'selectize-has-loading',
            errorClass = $select.attr('data-selectize-class-container-error') || 'selectize-error';

        instance.hidra_add_loading = function () {
            var $container = $('<span />', {'class': loadingClass + ' float-right'})
                .append($('<i />', {'class': 'spinner-border spinner-border-sm'}));
            instance.disable();
            instance.$control.addClass(loadingControlClass).append($container);
        };

        // triggerUpdate: makes the vendor measure the placeholder again after re-enabling
        instance.hidra_remove_loading = function (triggerUpdate) {
            if (triggerUpdate === true) {
                instance.$control_input.trigger('update');
            }
            $('.' + loadingClass, instance.$control).remove();
            instance.$control.removeClass(loadingControlClass);
            instance.enable();
        };

        // one message at a time, however many times it is called
        instance.hidra_show_error = function (message) {
            instance.hidra_remove_loading(false);
            instance.disable();
            if (instance.$wrapper.find('.' + errorClass).length) {
                return;
            }
            instance.$wrapper.append($('<strong />', {
                'class': errorClass + ' small text-danger',
                // the text the site initializer always showed; no jsi18n entry exists for it yet
                text: message ? message : 'Falha ao carregar informações'
            }));
        };

        // the vendor's clearOptions keeps the option of the selected item and only clears the
        // selection; the second call drops that option too, which is what a full reset wants
        instance.hidra_clear_options = function () {
            instance.clearOptions();
            instance.clearOptions();
        };
    }

    /** Runs the vendor on one <select> and applies what every branch needs afterwards. */
    function mount($el, options) {
        var placeholder = getPlaceholder($el),
            selectize;

        // The "Label: text" item needs the admin env ($.fn.nunjucks_env), which only exists
        // where the nunjucks alias of the fork is loaded; elsewhere the vendor's render stays.
        if (placeholder && $.fn.nunjucks_env) {
            options.render = $.extend({}, options.render, {item: selectPlaceholderTemplate});
        }

        // Page scripts get the last word on the settings.
        $el.trigger('selectize_pre_init', [options, placeholder]);

        // A form rendered again (site formsets) reaches here with a live instance; the vendor
        // would silently ignore the second call, so the old instance goes first.
        if ($el[0].selectize) {
            $el[0].selectize.destroy();
        }

        selectize = $el.selectize(options)[0].selectize;
        $el.data('selectize', selectize);
        attachHelpers($el, selectize);

        // The vendor stamps autocomplete="new-password" on the search input, which wakes Chrome's
        // password manager. Do not swap this for the bundled `autofill_disable` plugin: despite
        // the name it writes the very same "new-password" (selectize.js:3972).
        selectize.$control_input
            .attr('autocomplete', 'off')
            .removeAttr('autofill');

        // Inside the quick-form iframe the placeholder width is measured before layout settles;
        // a late `update` makes the vendor measure again.
        if (window.parent && window.parent.document) {
            window.setTimeout(function () {
                selectize.$control_input.trigger('update');
            }, 500);
        }
        return selectize;
    }

    /**
     * Changelist filters submit a multi-value select through a GET form and the server reads
     * `field__in=1,2`: one key, comma-joined. A native multiple select sends one `key=value`
     * per item and only the last one survives `request.GET.get`, so the FormData is rewritten
     * right before it leaves. POST forms are left alone: Django's M2M fields expect the
     * repeated key. Admin scope only: site filters (django-filter) expect the repeated key too.
     */
    function joinMultipleValuesOnSubmit(f) {
        if (!f.is('form') || (f.attr('method') || 'get').toLowerCase() !== 'get') {
            return;
        }
        if (!f.find('select[multiple]').length || f.data('xadminJoinsMultiple')) {
            return;
        }
        f.data('xadminJoinsMultiple', true);
        f.on('formdata', function (evt) {
            var data = evt.originalEvent && evt.originalEvent.formData;
            if (!data) {
                return;
            }
            // one select at a time: `.val()` on the whole set would only read the first one
            f.find('select[multiple]').each(function () {
                var values = $(this).val();
                if (values && values.length) {
                    data.set(this.name, values.join(','));
                }
            });
        });
    }

    /* ---- dependent-select: one branch for both scopes ---------------------------------- */

    function dependentField($select) {
        return $select.attr('data-field') || ($select.attr('name') || '').split('-').pop();
    }

    // The parent input: the child's name with the field swapped for the parent, prefix kept
    // ("<prefix>-state" asks for "<prefix>-section"), looked up in the same form.
    function dependentParentName($select) {
        var field = dependentField($select),
            name = $select.attr('name') || field;
        return name.slice(0, name.length - field.length) + $select.attr('data-dependent-parent');
    }

    function dependentParent($select) {
        var $form = $select.closest('form');
        return ($form.length ? $form : $(document)).find('[name="' + dependentParentName($select) + '"]').first();
    }

    // A select born empty (add form) has nowhere to put the initial the server computed for it,
    // so the endpoint sends it back and it becomes the selection -- only while the field is
    // still empty (on an edit the instance value is already there) and only for a real value:
    // 0 is legitimate, hence no truthiness test.
    function applyInitial(instance, initial) {
        if (instance.getValue() === '' && initial !== null && initial !== undefined) {
            instance.setValue(initial, true);
        }
    }

    function dependentOptions($select) {
        var field = dependentField($select),
            url = $select.attr('data-search-url'),
            emptyLabel = $select.attr('data-dependent-empty-label'),
            hideEmpty = $select.attr('data-dependent-hide-empty') === 'true';

        function deliver(instance, callback, items, initial) {
            // The value to keep: what the `change` handler saved before the double clear, or the
            // current selection (on the first load, what the server rendered). The vendor's
            // clearOptions keeps the OPTION of the selected value but drops the SELECTION, so the
            // value is put back afterwards; on the first load the server option survives, which is
            // what keeps a legacy value outside the fresh list selected.
            var keep = instance._dependentRestore || instance.getValue(),
                hadItems = items.length > 0;
            instance._dependentRestore = null;
            if (emptyLabel) {
                items = [{id: '', name: emptyLabel}].concat(items);
            }
            // the options the server rendered (the whole queryset, for the POST to validate) go
            // before the parent's come in
            instance.clearOptions();
            callback(items);
            if (keep) {
                if (instance.options[keep]) {
                    instance.setValue(keep, true);
                } else if (!hadItems) {
                    // an empty parent decides nothing: the value waits for the next parent
                    instance._dependentRestore = keep;
                }
            }
            applyInitial(instance, initial);
            // hide-empty is decided by the items the parent returned: the labelled empty option is
            // not an item, so a widget asking for both (riocard FAQ, #7614) still hides the group
            // while the parent has no children
            if (hideEmpty) {
                $select.closest('.form-group').toggleClass('d-none', !hadItems);
            }
        }

        return {
            valueField: 'id',
            labelField: 'name',
            searchField: 'name',
            create: false,
            // the options depend on the parent, so they are fetched on mount, not on focus
            preload: true,
            load: function (query, callback) {
                // with preload the vendor runs load while it is still being constructed, before
                // mount() has the instance and before the helpers are attached: `this` and the guards
                var instance = this,
                    parentValue = dependentParent($select).val() || '';
                if (!parentValue) {
                    // no parent, no request: the child stays empty (or shows the labelled empty option)
                    deliver(instance, callback, [], null);
                    return;
                }
                if (instance.hidra_add_loading) {
                    instance.hidra_add_loading();
                }
                $.ajax({
                    url: url,
                    dataType: 'json',
                    type: 'GET',
                    data: {'_dependent_field': field, '_dependent_parent': parentValue},
                    error: function () {
                        callback();
                        if (instance.hidra_show_error) {
                            instance.hidra_show_error();
                        }
                    },
                    success: function (res) {
                        // a bare list also counts: the manager's stages REST has another consumer
                        var items = Array.isArray(res) ? res : (res.items || []);
                        deliver(instance, callback, items, Array.isArray(res) ? null : res.initial);
                        if (instance.hidra_remove_loading) {
                            instance.hidra_remove_loading(true);
                        }
                    }
                });
            }
        };
    }

    // The contract with the parent widget is the DOM's own: whoever writes the parent value
    // fires `change` on its input (a native select and selectize already do). The handler is
    // delegated on the form by name -- formsets destroy and recreate the parent select, and a
    // cloned row brings a new node -- and reads the current instance of the child. The guard
    // is a property of the child node, not jQuery data: a clone copies the data, not properties.
    function bindDependentParent($select) {
        var $form = $select.closest('form'),
            scopeNode = $form[0] || document;
        if ($select[0]._dependentBound === scopeNode) {
            return;
        }
        $select[0]._dependentBound = scopeNode;
        $(scopeNode).on('change', '[name="' + dependentParentName($select) + '"]', function () {
            var instance = $select[0].selectize;
            if (!instance) {
                return;
            }
            // hidra_clear_options (double) drops even the selected option and resets
            // loadedSearches; the current value is kept aside and comes back in deliver()
            instance._dependentRestore = instance.getValue() || instance._dependentRestore;
            instance.hidra_clear_options();
            instance.onSearchChange('');
        });
    }

    /* ---- site scope: opt-in by class, the semantics of the Hidra widgets ------------------ */

    function siteOptions($select) {
        var options = {},
            render = nunjucksRenderers($select);
        if ($select.attr('multiple')) {
            $.extend(options, {
                plugins: pluginsFor($select, ['remove_button']),
                delimiter: ',',
                persist: false
            });
        } else if (!$select.hasClass('selectize-dont-allow-empty')) {
            // the empty option is a real choice on the site ("All states"), not a placeholder
            options.allowEmptyOption = true;
        }
        if (render) {
            options.render = render;
        }
        return options;
    }

    /* ---- render ------------------------------------------------------------------------- */

    $.fn.exform.renders.push(function (f) {
        var adminScope;

        if (!$.fn.selectize) {
            // without the vendor every <select> stays native: degraded, not broken
            return;
        }

        // The admin templates declare `class="exform"` on their forms; a quick-form wrapper or a
        // formset row arrives here as a plain container living inside one. Site forms never
        // carry the class, so outside it only the Hidra widgets (class selectize) opt in.
        adminScope = f.is('form.exform') || f.closest('form.exform').length > 0;

        if (!adminScope) {
            f.find('select.selectize:not(.selectize-off)').each(function () {
                var $el = $(this),
                    options = siteOptions($el),
                    instance;
                if ($el.hasClass('dependent-select')) {
                    $.extend(options, dependentOptions($el));
                }
                instance = mount($el, options);
                if ($el.hasClass('dependent-select')) {
                    bindDependentParent($el);
                }
                // a filled single keeps showing an input to type into; the site never wanted it
                if (instance.settings.mode === 'single' && instance.items.length) {
                    instance.hideInput();
                }
            });
            return;
        }

        f.find('.select-search').each(function () {
            var $el = $(this);
            mount($el, {
                valueField: 'id',
                labelField: '__str__',
                searchField: '__str__',
                create: false,
                maxItems: 1,
                // 'focus' loads once on the first open (selectize.js:1817); true keeps the eager
                // mount-time load for .select-preload
                preload: $el.hasClass('select-preload') ? true : 'focus',
                plugins: pluginsFor($el, ['clear_button', 'state_messages']),
                load: remoteLoader($el)
            });
        });

        f.find('select[multiple]:not(.selectize-off):not(.select-search)').each(function () {
            var $el = $(this),
                load = remoteLoader($el),
                options = {
                    // remove_button: the x on each chip; without it an item only leaves by keyboard
                    plugins: pluginsFor($el, ['remove_button', 'state_messages']),
                    // the default matches word starts only ("ção" misses "Fundação"); admin users
                    // know substring matching from the previous vendor
                    respect_word_boundaries: false,
                    // the default (1000) hides the surplus until the user types and reads as a
                    // missing option on a large M2M; null = no cap (the vendor only applies a number)
                    maxOptions: null,
                    // product decision (SEL-1, #7604): keep the dropdown open across picks and take
                    // the chosen options out of the list, as the changelist filters always did
                    hideSelected: true,
                    closeAfterSelect: false
                },
                render = nunjucksRenderers($el);

            if (load) {
                // large catalogue: only the chosen <option>s are in the HTML, the rest arrives as
                // the user types. 'focus' loads once on the first open, and what arrives is the
                // FIRST PAGE of the changelist, not the whole catalogue -- the server refines.
                options.valueField = 'id';
                options.labelField = '__str__';
                options.searchField = '__str__';
                options.create = false;
                options.preload = 'focus';
                options.load = load;
            }
            if (render) {
                options.render = render;
            }
            mount($el, options);
        });

        f.find('select.dependent-select').each(function () {
            var $el = $(this);
            mount($el, $.extend({plugins: pluginsFor($el, ['state_messages'])}, dependentOptions($el)));
            bindDependentParent($el);
        });

        f.find('select:not(.select-search):not(.selectize-off):not(.dependent-select):not([multiple])').each(function () {
            var $el = $(this),
                // state_messages ships with the selectize alias (vendors.py), so no guard is needed
                options = {plugins: pluginsFor($el, ['state_messages'])},
                render = nunjucksRenderers($el);
            if (render) {
                options.render = render;
            }
            mount($el, options);
        });

        joinMultipleValuesOnSubmit(f);
    });
})(jQuery);
