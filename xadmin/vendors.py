vendors = {
	"bootstrap": {
		'js': {
			'dev': [
				'xadmin/vendor/popper/popper.js',
				'xadmin/vendor/bootstrap/js/bootstrap.js',
			],
			'production': [
				'xadmin/vendor/popper/popper.min.js',
				'xadmin/vendor/bootstrap/js/bootstrap.min.js',
			],
			'cdn': 'https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/js/bootstrap.min.js'
		},
		'css': {
			'dev': [
				'xadmin/vendor/bootstrap/css/bootstrap.css',
			],
			'production': [
				'xadmin/vendor/bootstrap/css/bootstrap.min.css',
			],
			'cdn': 'https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/css/bootstrap.min.css'
		},
	},
	'jquery': {
		"js": {
			'dev': 'xadmin/vendor/jquery/jquery.js',
			'production': 'xadmin/vendor/jquery/jquery.min.js',
		}
	},
	'nunjucks': {
		"js": {
			'dev': ['xadmin/vendor/nunjucks/nunjucks.js',
			        'xadmin/js/nunjucks.engine.js'],
			'production': ['xadmin/vendor/nunjucks/nunjucks.min.js',
			               'xadmin/js/nunjucks.engine.js'],
		}
	},
	'jquery-ui-effect': {
		"js": {
			'dev': 'xadmin/vendor/jquery-ui/ui/effect.js',
			'production': 'xadmin/vendor/jquery-ui/ui/minified/effect.js'
		}
	},
	'jquery-ui-sortable': {
		"js": {
			'dev': [
				'xadmin/vendor/html5sortable/html5sortable.js',
				'xadmin/js/xadmin.plugin.sortable.js',
			],
			'production': [
				'xadmin/vendor/html5sortable/html5sortable.min.js',
				'xadmin/js/xadmin.plugin.sortable.js',
			]
		}
	},
	'datatables': {
		'lang': 'xadmin/vendor/datatables/i18n/%(lang)s.json',
		"js": {
			'dev': ['xadmin/vendor/datatables/datatables.js'],
			'production': ['xadmin/vendor/datatables/datatables.min.js']
		},
		"css": {
			'dev': ['xadmin/vendor/datatables/datatables.css'],
			'production': ['xadmin/vendor/datatables/datatables.min.css']
		}
	},
	"font-awesome": {
		"css": {
			'dev': [
				# fontawesome, brands, regular, solid
				'xadmin/vendor/font-awesome/css/all.css'
			],
			'production': [
				# fontawesome, brands, regular, solid
				'xadmin/vendor/font-awesome/css/all.min.css'
			]
		}
	},
	"timepicker": {
		"css": {
			'dev': 'xadmin/vendor/bootstrap-timepicker/css/timepicker.css',
			'production': 'xadmin/vendor/bootstrap-timepicker/css/timepicker.css',
		},
		"js": {
			'dev': 'xadmin/vendor/bootstrap-timepicker/js/bootstrap-timepicker.js',
			'production': 'xadmin/vendor/bootstrap-timepicker/js/bootstrap-timepicker.js',
		}
	},
	"clockpicker": {
		"css": {
			'dev': 'xadmin/vendor/clockpicker/dist/bootstrap-clockpicker.css',
			'production': 'xadmin/vendor/clockpicker/dist/bootstrap-clockpicker.min.css',
		},
		"js": {
			'dev': 'xadmin/vendor/clockpicker/dist/bootstrap-clockpicker.js',
			'production': 'xadmin/vendor/clockpicker/dist/bootstrap-clockpicker.min.js',
		}
	},
	"datepicker": {
		"css": {
			'dev': 'xadmin/vendor/bootstrap-datepicker/dist/css/bootstrap-datepicker.css'
		},
		"js": {
			'dev': 'xadmin/vendor/bootstrap-datepicker/dist/js/bootstrap-datepicker.js',
			'production': 'xadmin/vendor/bootstrap-datepicker/js/bootstrap-datepicker.min.js',
		}
	},
	"flot": {
		"js": {
			'dev': [
				'xadmin/vendor/flot/js/jquery.canvaswrapper.js',
				'xadmin/vendor/flot/js/jquery.flot.js',
				'xadmin/vendor/flot/js/jquery.flot.drawSeries.js',
				'xadmin/vendor/flot/js/jquery.colorhelpers.js',
				'xadmin/vendor/flot/js/jquery.flot.browser.js',
				'xadmin/vendor/flot/js/jquery.flot.uiConstants.js',
				'xadmin/vendor/flot/js/jquery.flot.saturated.js',
				'xadmin/vendor/flot/js/jquery.flot.pie.js',
				'xadmin/vendor/flot/js/jquery.flot.time.js',
				'xadmin/vendor/flot/js/jquery.flot.resize.js',
				'xadmin/vendor/flot/js/jquery.flot.categories.js']
		}
	},
	"image-gallery": {
		"css": {
			'dev': 'xadmin/vendor/blueimp-gallery/css/blueimp-gallery.css',
			'production': 'xadmin/vendor/blueimp-gallery/css/blueimp-gallery.min.css',
		},
		"js": {
			'dev': ['xadmin/vendor/blueimp-load-image/js/load-image.js',
			        'xadmin/vendor/blueimp-gallery/js/blueimp-gallery.js'],
			'production': ['xadmin/vendor/blueimp-load-image/js/load-image.all.min.js',
			               'xadmin/vendor/blueimp-gallery/js/blueimp-gallery.min.js']
		}
	},
	"selectize": {
		"css": {
			'dev': ['xadmin/vendor/selectize/css/selectize.css',
			        'xadmin/vendor/selectize/css/selectize.bootstrap4.css'],
			'production': ['xadmin/vendor/selectize/css/selectize.css',
			               'xadmin/vendor/selectize/css/selectize.bootstrap4.css'],
		},
		"js": {
			'dev': ['xadmin/vendor/selectize/js/selectize.js',
			        'xadmin/js/xadmin.selectize.state_messages.js'],
			'production': ['xadmin/vendor/selectize/js/selectize.min.js',
			               'xadmin/js/xadmin.selectize.state_messages.js']
		}
	},
}

# `select` is the alias every FK/M2M widget and filter asks for (widgets.py, filters.py,
# plugins/relfield.py). It is the SAME object as `selectize`, on purpose: a host project
# that hands the selectize stylesheet to its theme (plus_base empties selectize['css'])
# gets the swap for `select` too, without knowing this alias exists. Until v4.1.0 the
# alias also carried a second select vendor and its i18n catalog; nothing instantiates
# it since the host moved every select to selectize (#7608).
vendors['select'] = vendors['selectize']
