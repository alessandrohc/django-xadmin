#!/usr/bin/env python
# coding=utf-8
from io import open

from setuptools import setup

# Função para ler as dependências do requirements.txt
def load_requirements(filename='requirements.txt'):
    with open(filename, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
	name='xadmin',
	version='3.6.17',
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
	zip_safe=False,
	keywords=['admin', 'django', 'xadmin', 'bootstrap'],
	classifiers=[
		'Development Status :: 6 - Beta',
		'Environment :: Web Environment',
		'Framework :: Django',
		'Intended Audience :: Developers',
		'License :: OSI Approved :: BSD License',
		'Operating System :: OS Independent',
		"Programming Language :: JavaScript",
		'Programming Language :: Python',
		"Programming Language :: Python :: 3",
		"Programming Language :: Python :: 3.9",
		"Programming Language :: Python :: 3.10",
		"Topic :: Internet :: WWW/HTTP",
		"Topic :: Internet :: WWW/HTTP :: Dynamic Content",
		"Topic :: Software Development :: Libraries :: Python Modules",
	]
)