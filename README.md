# grow-ext-contentul

[![Build Status](https://travis-ci.org/grow/grow-ext-contentful.svg?branch=master)](https://travis-ci.org/grow/grow-ext-contentful)

Contentful extension for Grow. Binds Contentful collections to Grow
collections.

## Concept

(WIP)

## Usage

### Initial setup

1. Create an `extensions.txt` file within your pod.
1. Add to the file: `git+git://github.com/grow/grow-ext-contentful`
1. Run `grow install`.
1. Add the following section to `podspec.yaml`:

```
extensions:
  preprocessors:
  - extensions.contentful_ext.ContentfulPreprocessor

preprocessors:
- kind: contentful
  autorun: true
  inject: true
  space: exampleContentfulSpaceId
  keys:
    preview: exampleContentfulPreviewKey
    production: exampleContentfulProductionKey
  bind:
  - collection: /content/exampleModel1/
    contentModel: exampleModel1
  - collection: /content/exampleModel2/
    contentModel: exampleModel2
```
