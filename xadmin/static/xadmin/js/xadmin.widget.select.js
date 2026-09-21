/*
 * xadmin.widget.select.js -- the one place where an admin <select> becomes a selectize control.
 *
 * Every form xadmin renders through `.exform` passes through here: model forms, quick-forms,
 * dashboard widgets and the host project's changelist filter form. Three branches, one file:
 *
 *   .select-search     fk-ajax single select, options come from the changelist JSON
 *   select[multiple]   M2M widgets and multi-value filters (chips with a remove button)
 *   select             every other single select
 *
 * A widget or template tunes its control by attribute, never by shipping a script of its own:
 *
 *   data-search-url + data-choices   remote options (the `_q_`/`_cols` contract of the changelist)
 *   data-selectize-plugins           extra vendor plugins, space or comma separated ("drag_drop")
 *   data-selectize-min-length        read by the state_messages plugin (characters before searching)
 *   data-placeholder                 read by the vendor itself
 *   class selectize-off              leave the native <select> alone
 *   class select-placeholder         render the chosen item as "Label: text"
 *   class select-preload             (fk-ajax) load on mount instead of on the first focus
 *
 * Right before the vendor runs, `selectize_pre_init` fires on the <select> with the settings
 * object, so a page script can still adjust them without a fourth branch here. (#7611)
 */
;(function ($) {
    'use strict';

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
     * Remote loader for a select carrying `data-search-url`, or null when the options are
     * already in the HTML. The URL is the changelist of the related model; `data-choices`
     * appends the filters the field is restricted to.
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

    /** Runs the vendor on one <select> and applies what every branch needs afterwards. */
    function mount($el, options) {
        var placeholder = getPlaceholder($el),
            selectize;

        if (placeholder) {
            options.render = {item: selectPlaceholderTemplate};
        }

        // Page scripts get the last word on the settings (nothing listens today).
        $el.trigger('selectize_pre_init', [options, placeholder]);

        selectize = $el.selectize(options)[0].selectize;
        $el.data('selectize', selectize);

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
     * repeated key.
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

    $.fn.exform.renders.push(function (f) {
        if (!$.fn.selectize) {
            // without the vendor every <select> stays native: degraded, not broken
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
                };

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
            mount($el, options);
        });

        f.find('select:not(.select-search):not(.selectize-off):not([multiple])').each(function () {
            var $el = $(this);
            // state_messages ships with the selectize alias (vendors.py), so no guard is needed
            mount($el, {plugins: pluginsFor($el, ['state_messages'])});
        });

        joinMultipleValuesOnSubmit(f);
    });
})(jQuery);
