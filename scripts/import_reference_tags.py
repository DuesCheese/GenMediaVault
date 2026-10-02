"""Extract data only from the supplied local reference project; never execute its JavaScript."""
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path


def key(value):
    value = re.sub(r'\\([()\[\]{},:])', r'\1', value)
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('_', ' ').split())


def build(source, destination):
    dictionary_file, groups_file = source / 'nai-tag-ext/zh.js', source / 'nai-taglib.json'
    text = dictionary_file.read_text(encoding='utf-8-sig')
    dictionary = json.loads(text[text.index('{'):text.rindex('}') + 1])
    library = json.loads(groups_file.read_text(encoding='utf-8-sig'))
    entries, conflicts = {}, []
    for tag, translation in dictionary.items():
        normalized = key(tag)
        if normalized in entries:
            previous = entries[normalized]['translation']
            if translation != previous:
                # Keep all differing source meanings, rather than silently dropping an alias.
                entries[normalized]['translation'] = previous + ' / ' + translation
                conflicts.append(normalized)
        else:
            entries[normalized] = {'tag': tag, 'key': normalized, 'translation': translation, 'groups': []}
    for group in library['cats']:
        for tag in group['tags']:
            item = entries.setdefault(key(tag), {'tag': tag, 'key': key(tag), 'translation': '', 'groups': []})
            if group['name'] not in item['groups']:
                item['groups'].append(group['name'])
    payload = {'version': 1, 'source': source.name,
               'source_files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dictionary_file, groups_file)},
               'source_translation_count': len(dictionary), 'source_group_count': len(library['cats']),
               'merged_conflicts': len(conflicts), 'entries': sorted(entries.values(), key=lambda item: item['key'])}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'entries': len(entries), 'translated': sum(bool(e['translation']) for e in entries.values()),
                      'groups': len(library['cats']), 'merged_conflicts': len(conflicts)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('backend/genmedia/resources/tag-translations-v1.json'))
    args = parser.parse_args()
    build(args.source, args.output)
