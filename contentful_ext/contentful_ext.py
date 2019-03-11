import contentful
from grow.common import utils
from protorpc import messages
import datetime
import grow
import os
import yaml


class BindingMessage(messages.Message):
    collection = messages.StringField(1)
    content_type = messages.StringField(2)
    key = messages.StringField(3)


class VariationMessage(messages.Message):
    field = messages.StringField(1)
    path_format = messages.StringField(2)
    separator = messages.StringField(3)


class RewriteLocalesMessage(messages.Message):
    rewrite = messages.StringField(1)
    to = messages.StringField(2)


class ContentfulPreprocessor(grow.Preprocessor):
    KIND = 'contentful'
    _edit_entry_url_format = 'https://app.contentful.com/spaces/{space}/entries/{entry}'
    _edit_space_url_format = 'https://app.contentful.com/spaces/{space}/entries'
    _preview_endpoint = 'preview.contentful.com'

    class Config(messages.Message):
        space = messages.StringField(2)
        access_token = messages.StringField(3)
        bind = messages.MessageField(BindingMessage, 4, repeated=True)
        limit = messages.IntegerField(5)
        preview = messages.BooleanField(6, default=False)
        rewrite_locales = messages.MessageField(RewriteLocalesMessage, 7, repeated=True)
        variation = messages.MessageField(VariationMessage, 8)

    def normalize_locale(self, locale):
        # Converts a Contentful locale to a Grow (ICU) locale.
        clean_locale = locale.replace('-', '_')
        # Allow user-supplied rewriting of locales.
        if self.config.rewrite_locales:
            for item in self.config.rewrite_locales:
                if clean_locale == item.rewrite:
                    return item.to
        return clean_locale

    def _parse_entry(self, entry, key=None):
        """Parses an entry from Contentful."""
        default_locale = entry.default_locale
        raw_fields = entry.raw.get('fields', {})
        all_locales = set()

        # Use the page's "title" field to determine all the locales that
        # the page is in.
        # TODO(stevenle): this needs a better impl.
        locales_for_field = raw_fields.get('title', [])

        def _tag_localized_fields(obj, fields, tag_built_ins=False):
            for key in fields.keys():
                # locales_for_field = raw_fields.get(key, [])
                for locale in locales_for_field:
                    if default_locale == locale:
                        continue
                    tag_locale = self.normalize_locale(locale)
                    all_locales.add(tag_locale)
                    tagged_key = '{}@{}'.format(key, tag_locale)
                    # Support localized built-ins.
                    if tag_built_ins and key in ['title', 'category']:
                        tagged_key = '${}'.format(tagged_key)
                    localized_fields = obj.fields(locale)
                    if not localized_fields or key not in localized_fields:
                        continue
                    fields[tagged_key] = localized_fields[key]
            return fields

        def asset_representer(dumper, obj):
            # Assets are represented as structured objects, which can be
            # localized and also contain metadata.
            fields = obj.fields()
            fields = _tag_localized_fields(obj, fields)
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                fields)

        def link_representer(dumper, obj):
            tag = yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG
            fields = {}
            try:
                obj = obj.resolve(self.client)
                fields = obj.fields()
                fields = _tag_localized_fields(obj, fields)
            except (contentful.errors.NotFoundError, contentful.client.EntryNotFoundError) as e:
                self.pod.logger.error(e)
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                fields)

        def entry_representer(dumper, obj):
            fields = obj.fields()
            fields = _tag_localized_fields(obj, fields)
            fields['_content_type'] = obj.sys['content_type'].id
            fields['_id'] = obj.sys['id']
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                fields)

        # Round trip to YAML to normalize the content.
        yaml.add_representer(contentful.Asset, asset_representer)
        yaml.add_representer(contentful.Link, link_representer)
        yaml.add_representer(contentful.Entry, entry_representer)
        fields = entry.fields()
        fields = _tag_localized_fields(entry, fields, tag_built_ins=True)
        result = yaml.dump(fields, default_flow_style=False)
        fields = yaml.load(result)

        # Retrieve the key used for the basename from the fields, otherwise use
        # the enry's sys.id.
        normalized_key = fields.get(key) if key else entry.sys['id']
        basename = '{}.yaml'.format(normalized_key)

        # Support extracting the variation if it is enabled in podspec.yaml and
        # the slug matches the variation format. For example, a slug could be
        # named 'foo--bar', indicating this document is the 'bar' generation of
        # the 'foo' document.
        if self.config.variation:
            separator = self.config.variation.separator or '--'
            field = self.config.variation.field or 'slug'
            varied_path = self.config.variation.path_format
            if separator in fields.get(field, ''):
                parts_slug = fields[field]
                clean_slug, variation_name = parts_slug.split(separator)
                varied_path = varied_path.replace('{variation}', variation_name)
                fields['$path'] = varied_path
                fields['slug'] = clean_slug

        if 'slug' in fields:
            fields['$slug'] = fields.pop('slug')
        if 'title' in fields:
            fields['$title'] = fields.pop('title')
        if 'category' in fields:
            fields['$category'] = fields.pop('category')

        # Only add `$localization:locales` if the entry is localized, otherwise
        # just use the collection's default $localization, which is specified
        # by the user in Grow and not Contenful.
        if all_locales:
            all_locales.add(self.normalize_locale(default_locale))
            all_locales = list(all_locales)
            fields['$localization'] = {'locales': all_locales}
        return fields, basename

    def bind_collection(self, entries, collection_pod_path, key=None):
        """Binds a Grow collection to a Contentful collection."""
        collection = self.pod.get_collection(collection_pod_path)
        existing_pod_paths = [
            doc.pod_path for doc in collection.list_docs(recursive=False, inject=False)]
        new_pod_paths = []
        for i, entry in enumerate(entries):
            fields, basename = self._parse_entry(entry, key=key)
            # TODO: Ensure `create_doc` doesn't die if the file doesn't exist.
            path = os.path.join(collection.pod_path, basename)
            if not self.pod.file_exists(path):
                self.pod.write_yaml(path, {})
            doc = collection.create_doc(basename, fields=fields)
            new_pod_paths.append(doc.pod_path)
            self.pod.logger.info('Saved -> {}'.format(doc.pod_path))

        pod_paths_to_delete = set(existing_pod_paths) - set(new_pod_paths)
        for pod_path in pod_paths_to_delete:
            self.pod.delete_file(pod_path)
            self.pod.logger.info('Deleted -> {}'.format(pod_path))

    def run(self, *args, **kwargs):
        for binding in self.config.bind:
            content_type = binding.content_type
            entries = self.client.entries({
                'content_type': content_type,
                'include': 10,
                'locale': '*',
            })
            self.bind_collection(entries, binding.collection, key=binding.key)

    @property
    @utils.memoize
    def client(self):
        """Contentful API client."""
        access_token = self.config.access_token
        if self.config.preview:
            api_url = 'preview.contentful.com'
            return contentful.Client(
                    self.config.space,
                    access_token,
                    api_url=api_url,
                    reuse_entries=True,
                    max_include_resolution_depth=20)
        return contentful.Client(
                self.config.space,
                access_token,
                reuse_entries=True,
                max_include_resolution_depth=20)

    def _normalize_path(self, path):
        """Normalizes a collection path."""
        return path.rstrip('/')

    def get_edit_url(self, doc=None):
        """Returns the URL to edit in Contentful."""
        if doc:
            return ContentfulPreprocessor._edit_entry_url_format.format(
                space=self.config.space, entry=doc.base)
        return ContentfulPreprocessor._edit_space_url_format.format(
            space=self.config.space)
