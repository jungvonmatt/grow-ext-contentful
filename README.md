# grow-ext-contentul

[![Build Status](https://travis-ci.org/grow/grow-ext-contentful.svg?branch=master)](https://travis-ci.org/grow/grow-ext-contentful)

Contentful extension for Grow. Binds Contentful collections to Grow
collections.

## Concept

- Binds Contentful collections to Grow collections.
- Supports localized fields and localization.
- Currently limited to 100 entries per collection.

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
  space: exampleContentfulSpaceId
  access_token: exampleContentfulProductionKey
  # Uncomment to use the `preview` API.
  # access_token: exampleContentfulPreviewKey
  # preview: true
  bind:
  - collection: /content/exampleModel1/
    content_type: exampleModel1
  - collection: /content/exampleModel2/
    content_type: exampleModel2
```
