import contentful
from grow.common import utils
from protorpc import messages
import datetime
import grow
import os
import yaml


def normalize_locale(locale):
    # Converts a Contentful locale to a Grow (ICU) locale.
    return locale.replace('-', '_')


class BindingMessage(messages.Message):
    collection = messages.StringField(1)
    content_type = messages.StringField(2)


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

    def _parse_entry(self, entry):
        """Parses an entry from Contentful."""
        default_locale = entry.default_locale
        raw_fields = entry.raw.get('fields', {})

        def _tag_localized_fields(obj, fields):
            for key in fields.keys():
                locales_for_field = raw_fields.get(key, [])
                for locale in locales_for_field:
                    if default_locale == locale:
                        continue
                    tag_locale = normalize_locale(locale)
                    tagged_key = '{}@{}'.format(key, tag_locale)
                    localized_fields = obj.fields(locale)
                    if not localized_fields:
                        continue
                    fields[tagged_key] = localized_fields[key]
            return fields

        def asset_representer(dumper, obj):
            tag = yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG
            return dumper.represent_scalar(tag, obj.url())

        def link_representer(dumper, obj):
            tag = yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG
            obj = obj.resolve(self.client)
            if isinstance(obj, contentful.Asset):
                return dumper.represent_scalar(tag, obj.url())
            fields = obj.fields()
            fields = _tag_localized_fields(obj, fields)
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                fields)

        def entry_representer(dumper, obj):
            if hasattr(obj, 'resolve'):
                fields = obj.resolve(self.client)
            else:
                fields = obj.fields()
            fields = _tag_localized_fields(obj, fields)
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                fields)

        # Round trip to YAML to normalize the content.
        yaml.add_representer(contentful.Asset, asset_representer)
        yaml.add_representer(contentful.Link, link_representer)
        yaml.add_representer(contentful.Entry, entry_representer)
        fields = entry.fields()
        fields = _tag_localized_fields(entry, fields)
        result = yaml.dump(fields, default_flow_style=False)
        fields = yaml.load(result)
        if 'title' in fields:
            fields['$title'] = fields.pop('title')
        if 'slug' in fields:
            fields['$slug'] = fields.pop('slug')
        if 'category' in fields:
            fields['$category'] = fields.pop('category')
        basename = '{}.yaml'.format(entry.sys['id'])
        return fields, basename

    def bind_collection(self, entries, collection_pod_path):
        """Binds a Grow collection to a Contentful collection."""
        collection = self.pod.get_collection(collection_pod_path)
        existing_pod_paths = [
            doc.pod_path for doc in collection.list_docs(recursive=False, inject=False)]
        new_pod_paths = []
        for i, entry in enumerate(entries):
            fields, basename = self._parse_entry(entry)
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
            self.bind_collection(entries, binding.collection)

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

    def can_inject(self, doc=None, collection=None):
        if not self.injected:
            return False
        for binding in self.config.bind:
            if doc and doc.pod_path.startswith(binding.collection):
                return True
            if (collection and
                    self._normalize_path(collection.pod_path)
                    == self._normalize_path(binding.collection)):
                return True
        return False

    def inject(self, doc=None, collection=None):
        """Conditionally injects data into documents or a collection, without
        updating the filesystem. If doc is provided, the document's fields are
        injected. If collection is provided, returns a list of injected
        document instances."""
        if doc is not None:
            query = {'sys.id': doc.base}
            entry = self.cda.fetch(resources.Entry).where(query).first()
            if not entry:
                self.pod.logger.info('Contentful entry not found: {}'.format(query))
                return  # Corresponding doc not found in Contentful.
            fields, body, basename = self._parse_entry(entry)
            if isinstance(body, unicode):
                body = body.encode('utf-8')
            doc.inject(fields=fields, body=body)
            return doc
        elif collection is not None:
            entries = self.cda.fetch(resources.Entry).all()
            docs = []
            for binding in self.config.bind:
                if (self._normalize_path(collection.pod_path)
                        != self._normalize_path(binding.collection)):
                    continue
                docs += self.create_doc_instances(
                    entries, collection, binding.contentModel)
            return docs

    def create_doc_instances(self, entries, collection, contentful_model):
        docs = []
        for i, entry in enumerate(entries):
            if entry.sys['contentType']['sys']['id'] != contentful_model:
                continue
            fields, body, basename = self._parse_entry(entry)
            pod_path = os.path.join(collection.pod_path, basename)
            doc = collection.get_doc(pod_path)
            doc.inject(fields=fields, body=body)
            docs.append(doc)
        return docs

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
