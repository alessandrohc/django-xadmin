# coding=utf-8
"""A minimal app with a reversion-enabled admin and an inline.

It exists so the suite can exercise two code paths that no other test reaches:
``xversion.register_models()`` actually registering something (the positive branch of
the guard in ``XAdminConfig.ready()``), and ``_register_model()`` walking an inline's
reverse relation, which is the line that used to call the removed
``ForeignObjectRel.is_hidden()``.
"""
