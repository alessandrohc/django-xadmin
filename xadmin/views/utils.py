from django.utils.functional import cached_property


class ListQuery:
	session_key = 'LIST_QUERY'

	def __init__(self, request):
		self.request = request

	@cached_property
	def _session_key(self) -> str:
		return self.session_key if not self.request.is_ajax() else self.session_key + '_XHR'

	def set(self, value):
		self.request.session[self._session_key] = value

	def get(self, index=None):
		value = self.request.session[self._session_key]
		return value if index is None else value[index]

	def __bool__(self):
		return self._session_key in self.request.session
