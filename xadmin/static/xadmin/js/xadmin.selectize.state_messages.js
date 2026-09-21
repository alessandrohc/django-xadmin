/*
 * Selectize plugin "state_messages" — the state texts selectize 0.15.2 does not have. #7605
 *
 * The vendor is silent in three moments where the previous select vendor talked to the user:
 *   - while `load` runs, nothing is shown (the only trace is a `loading` class no stylesheet uses);
 *   - a search that ends with no option simply CLOSES the dropdown (`refreshOptions`);
 *   - there is no notion of a minimum query length — the only way is a silent guard inside `load`.
 *
 * The plugin appends a non-selectable message node to the dropdown for each of these states and
 * keeps the dropdown open while a message is shown. Texts come from the `/jsi18n/` catalog
 * (`gettext`, `ngettext`, `interpolate`), loaded by every admin page (xadmin/base.html); the
 * lookups are lazy, so the plugin works — in English — on a page without the catalog.
 *
 * Options (plugin options, or `data-selectize-min-length` on the <select>):
 *   minLength — queries shorter than this are not sent to `load` and the user is told how many
 *               characters are needed. 0 (default) disables the check. The empty query always
 *               passes: it is the first-page preload of the fk-ajax fields (`preload: 'focus'`).
 *
 * Usage: `plugins: ['state_messages']` or `plugins: {state_messages: {minLength: 3}}`.
 *
 * Ships with the `selectize` and `select` vendor aliases (vendors.py), right after selectize.js:
 * `Selectize.define` needs the vendor, and every page that has the vendor gets the plugin.
 */
;(function ($) {
    "use strict";

    if (!window.Selectize || !Selectize.plugins || Selectize.plugins.state_messages) {
        // no vendor on this page, or already defined — the file may come through more than one alias
        return;
    }

    // Hook on the `.selectize-control` wrapper while a load is in flight. No admin stylesheet reacts
    // to it today; it is the same class the site's form.widget.select.js uses for its own loading state.
    var LOADING_CLASS = 'selectize-has-loading';
    var MESSAGE_SELECTOR = '.selectize-state-message';
    var MESSAGE_CLASSES = 'selectize-state-message text-muted small px-3 py-2';
    // No `data-selectable`: the vendor's keyboard navigation and click handlers skip the node.
    // `role="status"` lets assistive technology announce the state change; the spinner is decorative.
    var MESSAGE_TEMPLATE =
        '<div class="{{ classes }}" role="status" data-state="{{ state }}">' +
        '{% if spinner %}<span class="spinner-border spinner-border-sm mr-2" aria-hidden="true"></span>{% endif %}' +
        '{{ text }}</div>';

    // Lazy i18n: resolved at call time so the catalog may be loaded after this file.
    function gettext(text) {
        return typeof window.gettext === 'function' ? window.gettext(text) : text;
    }

    function ngettext(singular, plural, count) {
        if (typeof window.ngettext === 'function') {
            return window.ngettext(singular, plural, count);
        }
        return count === 1 ? singular : plural;
    }

    function interpolate(fmt, context) {
        if (typeof window.interpolate === 'function') {
            return window.interpolate(fmt, context, true);
        }
        return fmt.replace(/%\((\w+)\)s/g, function (match, key) {
            return String(context[key]);
        });
    }

    var MESSAGES = {
        searching: function () {
            return gettext('Searching…');
        },
        no_results: function () {
            return gettext('No results found');
        },
        min_length: function (count) {
            return interpolate(
                ngettext('Type at least %(count)s character', 'Type at least %(count)s characters', count),
                {count: count});
        }
    };

    function renderMessage(state, text) {
        if ($.fn.nunjucks_env && typeof $.fn.nunjucks_env.renderString === 'function') {
            return $($.fn.nunjucks_env.renderString(MESSAGE_TEMPLATE, {
                classes: MESSAGE_CLASSES, state: state, text: text, spinner: state === 'searching'
            }));
        }
        // no template engine on the page: build the node through the DOM API, never from a string
        return $('<div>').addClass(MESSAGE_CLASSES).attr({role: 'status', 'data-state': state}).text(text);
    }

    Selectize.define('state_messages', function (options) {
        var self = this;
        var minLength = parseInt(options.minLength || self.$input.attr('data-selectize-min-length'), 10) || 0;

        function belowMinLength(query) {
            return minLength > 0 && query.length > 0 && query.length < minLength;
        }

        function wasSearched(query) {
            // `loadedSearches` is keyed by the raw input value; `refreshOptions` trims its query
            return self.loadedSearches.hasOwnProperty(query) ||
                self.loadedSearches.hasOwnProperty(self.$control_input.val());
        }

        // A non-empty query the `load` has not answered yet: the debounced `onSearchChange`
        // (`loadThrottle`) will fire it. On every keystroke the vendor refreshes the (local) options
        // BEFORE that, so an empty local result is not an answer yet — left alone, the vendor would
        // close the dropdown (and, in single mode with a value, hide the input) until the request
        // returns. Counting the pending search as "searching" keeps the dropdown open and the message
        // visible from the keystroke to the answer, as the previous vendor did.
        function searchPending(query) {
            return !!self.settings.load && query.length > 0 && !wasSearched(query);
        }

        // Which message the dropdown should show for the current query, or null for none.
        function resolveState(query) {
            if (belowMinLength(query)) {
                return 'min_length';
            }
            if (self.loading || searchPending(query)) {
                return 'searching';
            }
            if (!self.hasOptions && (!self.settings.load || wasSearched(query))) {
                return 'no_results';
            }
            return null;
        }

        function showMessage(state) {
            var text = state === 'min_length' ? MESSAGES.min_length(minLength) : MESSAGES[state]();
            self.$dropdown_content.find(MESSAGE_SELECTOR).remove();
            self.$dropdown_content.append(renderMessage(state, text));
        }

        self.refreshOptions = (function () {
            var original = self.refreshOptions;
            return function (triggerDropdown) {
                if (typeof triggerDropdown === 'undefined') {
                    triggerDropdown = true;
                }
                // Let the vendor render the options without deciding on the dropdown: with no option
                // it would close it, and the message needs it open — reopening after its close would
                // chain into `open() -> focus() -> onFocus -> refreshOptions` forever. The vendor's
                // render also replaces the whole dropdown content, so the message is re-rendered here.
                original.call(self, false);

                var state = resolveState(self.$control_input.val().trim());
                if (state) {
                    showMessage(state);
                }
                // The vendor's own tail, with "has a message" counting as content. A message alone only
                // opens a focused control: a background refresh (mount-time preload, blur) must not pop
                // the dropdown, and `open()` steals focus through `focus()`.
                if (triggerDropdown) {
                    if (self.hasOptions || state) {
                        if (!self.isOpen && (self.hasOptions || (self.isFocused && !self.isInputHidden))) {
                            self.open();
                        }
                    } else if (self.isOpen) {
                        self.close();
                    }
                }
            };
        })();

        self.load = (function () {
            var original = self.load;
            return function (fn) {
                self.$wrapper.addClass(LOADING_CLASS);
                original.call(self, fn);
                // The vendor only refreshes the dropdown when results ARRIVE; refresh now so the
                // "searching" message shows while the request is in flight (a synchronous callback
                // has already brought `loading` back to zero and shows nothing).
                if (self.loading) {
                    self.refreshOptions(self.isFocused && !self.isInputHidden);
                }
            };
        })();

        self.on('load', function (results) {
            if (!self.loading) {
                self.$wrapper.removeClass(LOADING_CLASS);
            }
            // An empty answer leaves the dropdown untouched by the vendor: refresh so "searching"
            // gives way to "no results". A non-empty one was already refreshed by the vendor.
            if (!results || !results.length) {
                self.refreshOptions(self.isFocused && !self.isInputHidden);
            }
        });

        self.onSearchChange = (function () {
            // Already debounced by the constructor (`loadThrottle`): the length check below runs on
            // every keystroke, the `load` still waits.
            var original = self.onSearchChange;
            return function (value) {
                if (belowMinLength(String(value || '').trim())) {
                    // Not sent to `load` and not cached in `loadedSearches`; the message itself is
                    // rendered by `refreshOptions`, which the vendor calls right after this (onInput).
                    return;
                }
                return original.apply(self, arguments);
            };
        })();
    });

})(jQuery);
