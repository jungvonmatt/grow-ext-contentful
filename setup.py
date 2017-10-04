from setuptools import setup


setup(
    name='grow-ext-contentful',
    version='1.0.1',
    license='MIT',
    author='Grow Authors',
    author_email='hello@grow.io',
    include_package_data=False,
    packages=[
        'contentful_ext',
    ],
    install_requires=[
        'contentful.py==0.9.3',
    ],
)
