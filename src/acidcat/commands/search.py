"""
acidcat search -- text-based search and tagging for audio samples.
"""

import json
import os
import sys

from acidcat.core.formats import output


class AudioTextSearch:
    """Text-based search system for audio samples."""

    def __init__(self, csv_path, tags_path=None):
        import pandas as pd
        self.csv_path = csv_path
        self.df = pd.read_csv(csv_path)
        self.tags_path = tags_path or csv_path.replace('.csv', '_tags.json')
        self.tags_db = self._load_tags()

    def _load_tags(self):
        if os.path.exists(self.tags_path):
            with open(self.tags_path, 'r') as f:
                return json.load(f)
        return {}

    def _save_tags(self):
        with open(self.tags_path, 'w') as f:
            json.dump(self.tags_db, f, indent=2)

    def add_description(self, filename, description, tags=None, overwrite=False):
        matches = self.df[self.df['filename'].str.contains(filename, case=False)]
        if matches.empty:
            print(f"No samples found matching: {filename}")
            return False
        if len(matches) > 1:
            print(f"Multiple matches for '{filename}':")
            for _, row in matches.iterrows():
                print(f"  {row['filename']}")
            return False

        sample_path = matches.iloc[0]['filename']
        if sample_path in self.tags_db and not overwrite:
            print(f"Description already exists for {sample_path} (use --overwrite).")
            return False

        self.tags_db[sample_path] = {
            'description': description,
            'tags': tags or [],
            'bpm': matches.iloc[0].get('bpm'),
            'duration': matches.iloc[0].get('duration_sec'),
            'key': matches.iloc[0].get('smpl_root_key') or matches.iloc[0].get('acid_root_note'),
        }
        self._save_tags()
        print(f"Tagged: {os.path.basename(sample_path)}")
        return True

    def search_by_text(self, query):
        query_words = query.lower().split()
        matches = []
        for sample_path, data in self.tags_db.items():
            score = 0
            desc = data.get('description', '').lower()
            for w in query_words:
                if w in desc:
                    score += 1
            tags = [t.lower() for t in data.get('tags', [])]
            for w in query_words:
                for t in tags:
                    if w in t:
                        score += 1
            if score > 0:
                matches.append({
                    'filename': sample_path,
                    'description': data.get('description', ''),
                    'tags': data.get('tags', []),
                    'bpm': data.get('bpm'),
                    'duration': data.get('duration'),
                    'relevance': score,
                })
        matches.sort(key=lambda x: x['relevance'], reverse=True)
        return matches

    def search_by_tags(self, tags):
        if isinstance(tags, str):
            tags = [tags]
        tags_lower = [t.lower() for t in tags]
        matches = []
        for path, data in self.tags_db.items():
            sample_tags = [t.lower() for t in data.get('tags', [])]
            if any(st in sample_tags for st in tags_lower):
                matches.append({
                    'filename': path,
                    'description': data.get('description', ''),
                    'tags': data.get('tags', []),
                    'matching_tags': [t for t in data.get('tags', []) if t.lower() in tags_lower],
                })
        return matches

    def get_all_tags(self):
        all_tags = set()
        for data in self.tags_db.values():
            all_tags.update(data.get('tags', []))
        return sorted(all_tags)

    def get_stats(self):
        total = len(self.df)
        described = len(self.tags_db)
        return {
            'total_samples': total,
            'described_samples': described,
            'coverage': f"{described / total * 100:.1f}%" if total else "0%",
            'unique_tags': len(self.get_all_tags()),
        }

    def export_enhanced_csv(self, output_path=None):
        if output_path is None:
            output_path = self.csv_path.replace('.csv', '_enhanced.csv')
        enhanced = self.df.copy()
        enhanced['description'] = ''
        enhanced['tags'] = ''
        for i, row in enhanced.iterrows():
            fn = row['filename']
            if fn in self.tags_db:
                data = self.tags_db[fn]
                enhanced.at[i, 'description'] = data.get('description', '')
                enhanced.at[i, 'tags'] = ', '.join(data.get('tags', []))
        enhanced.to_csv(output_path, index=False)
        print(f"Exported: {output_path}")
        return output_path


def register(subparsers):
    p = subparsers.add_parser("search", help="Text-based search and tagging.")
    p.add_argument("csv_path", help="CSV file with audio features.")

    sub = p.add_subparsers(dest="subcmd")

    # query
    q_p = sub.add_parser("query", help="Search by text.")
    q_p.add_argument("text", help="Search query.")

    # tags
    t_p = sub.add_parser("tags", help="Search by tags.")
    t_p.add_argument("tag_list", help="Comma-separated tags.")

    # tag (add)
    a_p = sub.add_parser("tag", help="Add description/tags to a sample.")
    a_p.add_argument("filename", help="Sample filename (partial match OK).")
    a_p.add_argument("description", help="Text description.")
    a_p.add_argument("--tags", help="Comma-separated tags.")
    a_p.add_argument("--overwrite", action="store_true")

    # interactive
    sub.add_parser("interactive", help="Interactive tagging session.")

    # stats
    sub.add_parser("stats", help="Show database statistics.")

    # export
    e_p = sub.add_parser("export", help="Export enhanced CSV.")
    e_p.add_argument("-o", "--output", help="Output path.")

    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"])
    p.set_defaults(func=run)


def run(args):
    engine = AudioTextSearch(args.csv_path)
    subcmd = getattr(args, 'subcmd', None)

    if subcmd == "query":
        results = engine.search_by_text(args.text)
        if results:
            for r in results:
                print(f"{os.path.basename(r['filename'])}")
                print(f"  {r['description']}")
                print(f"  Tags: {', '.join(r['tags'])}  Score: {r['relevance']}")
                print()
        else:
            print("No matches.")
        return 0

    elif subcmd == "tags":
        tags = [t.strip() for t in args.tag_list.split(",")]
        results = engine.search_by_tags(tags)
        for r in results:
            print(f"{os.path.basename(r['filename'])}  [{', '.join(r['matching_tags'])}]")
        return 0

    elif subcmd == "tag":
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        engine.add_description(args.filename, args.description, tags, args.overwrite)
        return 0

    elif subcmd == "interactive":
        _interactive(engine)
        return 0

    elif subcmd == "stats":
        stats = engine.get_stats()
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return 0

    elif subcmd == "export":
        out = getattr(args, 'output', None)
        engine.export_enhanced_csv(out)
        return 0

    else:
        print("Usage: acidcat search CSV_PATH {query|tags|tag|interactive|stats|export}", file=sys.stderr)
        return 1


def _interactive(engine):
    """Interactive tagging REPL."""
    print("=== acidcat interactive tagging ===")
    print("Commands: tag, search, tags, list, stats, export, quit\n")
    while True:
        try:
            cmd = input("acidcat> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd:
            continue
        if cmd in ('quit', 'q'):
            break
        elif cmd in ('help', 'h'):
            print("  tag <file>   - add description/tags")
            print("  search <q>   - text search")
            print("  tags <t1,t2> - tag search")
            print("  list         - list all samples")
            print("  stats        - database statistics")
            print("  export       - export enhanced CSV")
            print("  quit         - exit")
        elif cmd.startswith('tag '):
            fn = cmd[4:].strip()
            desc = input("Description: ").strip()
            tags_in = input("Tags (comma-sep): ").strip()
            tags = [t.strip() for t in tags_in.split(',') if t.strip()]
            engine.add_description(fn, desc, tags)
        elif cmd.startswith('search '):
            results = engine.search_by_text(cmd[7:].strip())
            for r in results:
                print(f"  {os.path.basename(r['filename'])}: {r['description']}")
        elif cmd.startswith('tags '):
            tags = [t.strip() for t in cmd[5:].split(',')]
            for r in engine.search_by_tags(tags):
                print(f"  {os.path.basename(r['filename'])} [{', '.join(r['matching_tags'])}]")
        elif cmd == 'list':
            for _, row in engine.df.iterrows():
                fn = os.path.basename(row['filename'])
                tagged = "[+]" if row['filename'] in engine.tags_db else "[ ]"
                print(f"  {tagged} {fn}")
        elif cmd == 'stats':
            for k, v in engine.get_stats().items():
                print(f"  {k}: {v}")
        elif cmd == 'export':
            engine.export_enhanced_csv()
        else:
            print("Unknown command. Type 'help'.")
