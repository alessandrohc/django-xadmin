from django.template.context import RequestContext, Context

import collections


def get_context_dict(context):
	"""
	 Contexts in django version 1.9+ must be dictionaries. As xadmin has a legacy with older versions of django,
	the function helps the transition by converting the [RequestContext, Context] object to the dictionary when necessary.
	:param context: RequestContext
	:return: dict
	"""
	if isinstance(context, RequestContext):
		ctx = context.flatten()
	elif isinstance(context, Context):
		ctx = context.flatten()
	else:
		ctx = context
	return ctx


class OrderedDefaultDict(collections.OrderedDict):
	"""
	An OrderedDict subclass that provides default values for missing keys,
	similar to defaultdict, while preserving the order of insertion.

	Attributes:
	-----------
	default_factory : callable, optional
		A function that provides the default value for the dictionary when a key is missing.
		If None, accessing a missing key will raise a KeyError.

	Methods:
	--------
	__missing__(key):
		Called when a key is not found. If `default_factory` is set, it creates a new
		default value, assigns it to the key, and returns the value. If `default_factory`
		is None, raises a KeyError.

	__repr__():
		Returns a string representation of the `OrderedDefaultDict`, including the `default_factory`
		if it is set.

	Example:
	--------
	>>> ordered_defaultdict = OrderedDefaultDict(list)
	>>> ordered_defaultdict['missing_key']
	[]
	>>> ordered_defaultdict['existing_key'] = [1, 2, 3]
	>>> ordered_defaultdict['existing_key']
	[1, 2, 3]
	"""

	def __init__(self, default_factory=None, *args, **kwargs):
		"""
		Initializes the OrderedDefaultDict.

		Parameters:
		-----------
		default_factory : callable, optional
			A function that provides default values for missing keys. If None, accessing a missing key will raise a KeyError.

		*args, **kwargs :
			Additional positional and keyword arguments are passed to the OrderedDict constructor.
		"""
		super().__init__(*args, **kwargs)
		self.default_factory = default_factory

	def __missing__(self, key):
		"""
		Method called when the key is not present in the dictionary.

		Parameters:
		-----------
		key : any hashable type
			The key that is missing in the dictionary.

		Returns:
		--------
		value : any type
			The value corresponding to the missing key, created by default_factory.

		Raises:
		-------
		KeyError
			If `default_factory` is None, raises KeyError.
		"""
		if self.default_factory is None:
			raise KeyError(key)
		self[key] = self.default_factory()
		return self[key]

	def __repr__(self):
		"""
		Returns a string representation of the OrderedDefaultDict, showing the
		default factory and the dictionary contents.

		Returns:
		--------
		str
			A string representation of the OrderedDefaultDict.
		"""
		return f'{self.__class__.__name__}({self.default_factory}, {super().__repr__()})'