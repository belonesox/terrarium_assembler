#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [ 
    'easydict',
    'pytictoc',
#    'nuitka',
    'file-magic',
    'jinja2', 
    'wheel_filename',
]

setup_requirements = []

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
        ],
    },
    install_requires=requirements,
    license="MIT license",
    long_description=readme + '\n\n' + history,
    include_package_data=True,
    keywords='terrarium_assembler',
    name='terrarium_assembler',
    packages=find_packages(include=['terrarium_assembler', 'terrarium_assembler.*']),
    setup_requires=setup_requirements,
    test_suite='tests',
    tests_require=test_requirements,
    url='https://github.com/belonesox/terrarium_assembler',
    version='0.1.1',
    zip_safe=False,
)
