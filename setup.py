#!/usr/bin/env python
# coding=utf-8
from io import open

from setuptools import setup

# Read the dependency list that setup() installs, from requirements.txt
def load_requirements(filename='requirements.txt'):
    with open(filename, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
	name='xadmin',
	version='3.7.0',
	description='Drop-in replacement of Django admin comes with lots of goodies, '
	            'fully extensible with plugin support, pretty UI based on Twitter Bootstrap.',
	long_description=open('README.rst', encoding='utf-8').read(),
	author='sshwsfc',
	author_email='sshwsfc@gmail.com',
	license=open('LICENSE', encoding='utf-8').read(),
	url='https://github.com/alessandrohc/django-xadmin',
	download_url='https://github.com/alessandrohc/django-xadmin/archive/python3-dj32.zip',
	packages=['xadmin', 'xadmin.migrations', 'xadmin.plugins', 'xadmin.templatetags', 'xadmin.views'],
	include_package_data=True,
	install_requires=load_requirements(),
	extras_require={
		'Excel': ['xlwt', 'xlsxwriter'],
		'Reversion': ['django-reversion>=5.0.2'],
	},
	python_requires='>=3.10',
	zip_safe=False,
	keywords=['admin', 'django', 'xadmin', 'bootstrap'],
	classifiers=[
		'Development Status :: 5 - Production/Stable',
		'Environment :: Web Environment',
		'Framework :: Django',
		'Framework :: Django :: 4.2',
		'Framework :: Django :: 5.0',
		'Framework :: Django :: 5.1',
		'Framework :: Django :: 5.2',
		'Intended Audience :: Developers',
		'License :: OSI Approved :: BSD License',
		'Operating System :: OS Independent',
		"Programming Language :: JavaScript",
		'Programming Language :: Python',
		"Programming Language :: Python :: 3",
		"Programming Language :: Python :: 3 :: Only",
		"Programming Language :: Python :: 3.10",
		"Programming Language :: Python :: 3.11",
		"Programming Language :: Python :: 3.12",
		"Programming Language :: Python :: 3.13",
		"Programming Language :: Python :: 3.14",
		"Topic :: Internet :: WWW/HTTP",
		"Topic :: Internet :: WWW/HTTP :: Dynamic Content",
		"Topic :: Software Development :: Libraries :: Python Modules",
	]
)