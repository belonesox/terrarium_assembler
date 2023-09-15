#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [ 
    'easydict',
    'version_utils',
    'python-dateutil',
    'pytictoc',
    'file-magic',
    'python-magic',
    'jinja2', 
    'pyyaml',
    'packaging',
    'prettytable',
    'pipenv',
    'imohash',
    'pydantic',
    'xmltodict',
    'wheel_filename',
    'requirements-parser'
]

test_requirements = ['pytest>=3', ]

setup(
    author="Stas Fomin",
    author_email='stas-fomin@yandex.ru',
    python_requires='>=3.7',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
    description="Generate Portable Linux Applications, just portable folders",
    entry_points={
        'console_scripts': [
            'terrarium_assembler=terrarium_assembler.cli:main',
	    'tas=terrarium_assembler.cli:main'
        ],
    },
    install_requires=requirements,
    license="MIT license",
    long_description=readme + '\n\n' + history,
    include_package_data=True,
    keywords='terrarium_assembler',
    name='terrarium_assembler',
    packages=find_packages(include=['terrarium_assembler', 'terrarium_assembler.*']),
    version_config={
        "enabled": True,
#        "template": 	"{tag}",
        "dev_template": "{tag}.dev{ccount}",
        "dirty_template": "{tag}.dev{ccount}",
    },
    setup_requires=['setuptools-git-versioning'],
    test_suite='tests',
    tests_require=test_requirements,
    url='https://github.com/belonesox/terrarium_assembler',
    zip_safe=False,
)
