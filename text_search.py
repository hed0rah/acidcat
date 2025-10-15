#!/usr/bin/env python3
"""
text_search.py

Text-based search and tagging system for audio samples.
Allows adding descriptions, tags, and searching by text queries.
"""

import pandas as pd
import json
import os
import re
from collections import defaultdict
import argparse


class AudioTextSearch:
    """
    Text-based search system for audio samples.
    """

    def __init__(self, csv_path, tags_path=None):
        """
        Initialize with audio metadata CSV and optional tags file.

        Args:
            csv_path: Path to CSV file with audio features
            tags_path: Path to JSON file with sample descriptions/tags
        """
        self.csv_path = csv_path
        self.df = pd.read_csv(csv_path)
        self.tags_path = tags_path or csv_path.replace('.csv', '_tags.json')

        # Load or create tags database
        self.tags_db = self._load_tags()

    def _load_tags(self):
        """Load tags database from JSON file."""
        if os.path.exists(self.tags_path):
            with open(self.tags_path, 'r') as f:
                return json.load(f)
        else:
            return {}

    def _save_tags(self):
        """Save tags database to JSON file."""
        with open(self.tags_path, 'w') as f:
            json.dump(self.tags_db, f, indent=2)

    def add_description(self, filename, description, tags=None, overwrite=False):
        """
        Add description and tags to a sample.

        Args:
            filename: Sample filename (can be partial match)
            description: Text description of the sample
            tags: List of tags (genres, moods, instruments, etc.)
            overwrite: Whether to overwrite existing description
        """
        # Find matching samples
        matches = self.df[self.df['filename'].str.contains(filename, case=False)]
        if matches.empty:
            print(f"No samples found matching: {filename}")
            return False

        if len(matches) > 1:
            print(f"Multiple matches found for '{filename}':")
            for i, row in matches.iterrows():
                print(f"  {i}: {row['filename']}")
            print("Please be more specific or use the full filename.")
            return False

        sample_path = matches.iloc[0]['filename']

        # Check if description already exists
        if sample_path in self.tags_db and not overwrite:
            print(f"Description already exists for {sample_path}")
            print(f"Current: {self.tags_db[sample_path]}")
            print("Use overwrite=True to replace it.")
            return False

        # Create entry
        entry = {
            'description': description,
            'tags': tags or [],
            'bpm': matches.iloc[0].get('bpm'),
            'duration': matches.iloc[0].get('duration_sec'),
            'key': matches.iloc[0].get('smpl_root_key') or matches.iloc[0].get('acid_root_note')
        }

        self.tags_db[sample_path] = entry
        self._save_tags()

        print(f"Added description for: {os.path.basename(sample_path)}")
        return True

    def search_by_text(self, query, search_fields=None):
        """
        Search samples by text query.

        Args:
            query: Search query string
            search_fields: Fields to search in ('description', 'tags', 'all')

        Returns:
            List of matching samples with their descriptions
        """
        if search_fields is None:
            search_fields = ['description', 'tags']

        query_words = query.lower().split()
        matches = []

        for sample_path, data in self.tags_db.items():
            score = 0

            # Search in description
            if 'description' in search_fields:
                description = data.get('description', '').lower()
                for word in query_words:
                    if word in description:
                        score += 1

            # Search in tags
            if 'tags' in search_fields:
                tags = [tag.lower() for tag in data.get('tags', [])]
                for word in query_words:
                    for tag in tags:
                        if word in tag:
                            score += 1

            if score > 0:
                # Get audio features from main DataFrame
                sample_match = self.df[self.df['filename'] == sample_path]
                if not sample_match.empty:
                    result = {
                        'filename': sample_path,
                        'description': data.get('description', ''),
                        'tags': data.get('tags', []),
                        'bpm': data.get('bpm'),
                        'duration': data.get('duration'),
                        'key': data.get('key'),
                        'relevance_score': score
                    }
                    matches.append(result)

        # Sort by relevance score
        matches.sort(key=lambda x: x['relevance_score'], reverse=True)
        return matches

    def search_by_tags(self, tags):
        """
        Search samples by specific tags.

        Args:
            tags: List of tags to search for

        Returns:
            List of matching samples
        """
        if isinstance(tags, str):
            tags = [tags]

        tags_lower = [tag.lower() for tag in tags]
        matches = []

        for sample_path, data in self.tags_db.items():
            sample_tags = [tag.lower() for tag in data.get('tags', [])]

            # Check if any of the search tags match
            if any(search_tag in sample_tags for search_tag in tags_lower):
                sample_match = self.df[self.df['filename'] == sample_path]
                if not sample_match.empty:
                    result = {
                        'filename': sample_path,
                        'description': data.get('description', ''),
                        'tags': data.get('tags', []),
                        'bpm': data.get('bpm'),
                        'duration': data.get('duration'),
                        'key': data.get('key'),
                        'matching_tags': [tag for tag in data.get('tags', [])
                                        if tag.lower() in tags_lower]
                    }
                    matches.append(result)

        return matches

    def get_all_tags(self):
        """Get all unique tags in the database."""
        all_tags = set()
        for data in self.tags_db.values():
            all_tags.update(data.get('tags', []))
        return sorted(list(all_tags))

    def get_samples_by_criteria(self, bpm_range=None, duration_range=None,
                               key=None, has_description=None):
        """
        Search samples by musical criteria combined with text data.

        Args:
            bpm_range: Tuple of (min_bpm, max_bpm)
            duration_range: Tuple of (min_duration, max_duration)
            key: Musical key to search for
            has_description: True/False to filter by description presence

        Returns:
            List of matching samples
        """
        matches = []

        for sample_path, data in self.tags_db.items():
            # Check criteria
            if bpm_range:
                bpm = data.get('bpm')
                if not bpm or not (bpm_range[0] <= bpm <= bpm_range[1]):
                    continue

            if duration_range:
                duration = data.get('duration')
                if not duration or not (duration_range[0] <= duration <= duration_range[1]):
                    continue

            if key:
                sample_key = data.get('key')
                if not sample_key or sample_key != key:
                    continue

            if has_description is not None:
                has_desc = bool(data.get('description', '').strip())
                if has_desc != has_description:
                    continue

            matches.append({
                'filename': sample_path,
                'description': data.get('description', ''),
                'tags': data.get('tags', []),
                'bpm': data.get('bpm'),
                'duration': data.get('duration'),
                'key': data.get('key')
            })

        return matches

    def export_enhanced_csv(self, output_path=None):
        """
        Export CSV with original audio features plus text descriptions and tags.

        Args:
            output_path: Path for output CSV (auto-generated if None)
        """
        if output_path is None:
            output_path = self.csv_path.replace('.csv', '_enhanced.csv')

        # Create enhanced dataframe
        enhanced_df = self.df.copy()
        enhanced_df['description'] = ''
        enhanced_df['tags'] = ''

        # Add text data
        for i, row in enhanced_df.iterrows():
            filename = row['filename']
            if filename in self.tags_db:
                data = self.tags_db[filename]
                enhanced_df.at[i, 'description'] = data.get('description', '')
                enhanced_df.at[i, 'tags'] = ', '.join(data.get('tags', []))

        enhanced_df.to_csv(output_path, index=False)
        print(f"Exported enhanced CSV to: {output_path}")
        return output_path

    def get_stats(self):
        """Get statistics about the text database."""
        total_samples = len(self.df)
        described_samples = len(self.tags_db)
        total_tags = len(self.get_all_tags())

        stats = {
            'total_samples': total_samples,
            'described_samples': described_samples,
            'description_coverage': described_samples / total_samples * 100,
            'total_unique_tags': total_tags,
            'avg_tags_per_sample': sum(len(data.get('tags', [])) for data in self.tags_db.values()) / max(described_samples, 1)
        }

        return stats


def interactive_tagging_session(csv_path):
    """
    Interactive session for adding descriptions and tags to samples.
    """
    search_engine = AudioTextSearch(csv_path)

    print("=== Interactive Audio Sample Tagging ===")
    print("Commands: tag, search, list, stats, help, quit")
    print()

    while True:
        command = input(">>> ").strip().lower()

        if command == 'quit' or command == 'q':
            break

        elif command == 'help' or command == 'h':
            print("Available commands:")
            print("  tag <filename> - Add description and tags to a sample")
            print("  search <query> - Search samples by text")
            print("  tags <tag1,tag2> - Search by specific tags")
            print("  list - List all samples")
            print("  stats - Show database statistics")
            print("  export - Export enhanced CSV")
            print("  quit - Exit")

        elif command.startswith('tag '):
            filename = command[4:].strip()
            if not filename:
                print("Usage: tag <filename>")
                continue

            print(f"Adding description for: {filename}")
            description = input("Description: ").strip()
            tags_input = input("Tags (comma-separated): ").strip()
            tags = [tag.strip() for tag in tags_input.split(',') if tag.strip()]

            search_engine.add_description(filename, description, tags)

        elif command.startswith('search '):
            query = command[7:].strip()
            if not query:
                print("Usage: search <query>")
                continue

            results = search_engine.search_by_text(query)
            if results:
                print(f"Found {len(results)} matches:")
                for result in results:
                    print(f"  {os.path.basename(result['filename'])}")
                    print(f"    Description: {result['description']}")
                    print(f"    Tags: {', '.join(result['tags'])}")
                    print(f"    BPM: {result['bpm']}, Duration: {result['duration']:.1f}s")
                    print()
            else:
                print("No matches found.")

        elif command.startswith('tags '):
            tags_input = command[5:].strip()
            if not tags_input:
                print("Usage: tags <tag1,tag2>")
                continue

            tags = [tag.strip() for tag in tags_input.split(',')]
            results = search_engine.search_by_tags(tags)

            if results:
                print(f"Found {len(results)} samples with tags: {', '.join(tags)}")
                for result in results:
                    print(f"  {os.path.basename(result['filename'])}")
                    print(f"    Matching tags: {', '.join(result['matching_tags'])}")
                    print()
            else:
                print("No samples found with those tags.")

        elif command == 'list':
            print("Available samples:")
            for i, row in search_engine.df.iterrows():
                filename = os.path.basename(row['filename'])
                has_desc = row['filename'] in search_engine.tags_db
                status = "[Tagged]" if has_desc else "[No description]"
                print(f"  {filename} {status}")

        elif command == 'stats':
            stats = search_engine.get_stats()
            print("Database Statistics:")
            print(f"  Total samples: {stats['total_samples']}")
            print(f"  Described samples: {stats['described_samples']}")
            print(f"  Description coverage: {stats['description_coverage']:.1f}%")
            print(f"  Total unique tags: {stats['total_unique_tags']}")
            print(f"  Average tags per sample: {stats['avg_tags_per_sample']:.1f}")

        elif command == 'export':
            output_path = search_engine.export_enhanced_csv()
            print(f"Enhanced CSV exported to: {output_path}")

        else:
            print("Unknown command. Type 'help' for available commands.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Text-based audio sample search and tagging")
    parser.add_argument("csv_path", help="Path to CSV file with audio features")

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Interactive mode
    interactive_parser = subparsers.add_parser('interactive', help='Interactive tagging session')

    # Add description
    tag_parser = subparsers.add_parser('tag', help='Add description and tags')
    tag_parser.add_argument("filename", help="Sample filename (can be partial)")
    tag_parser.add_argument("description", help="Sample description")
    tag_parser.add_argument("--tags", help="Comma-separated tags")

    # Search
    search_parser = subparsers.add_parser('search', help='Search by text')
    search_parser.add_argument("query", help="Search query")

    # Search by tags
    tags_parser = subparsers.add_parser('tags', help='Search by specific tags')
    tags_parser.add_argument("tags", help="Comma-separated tags to search for")

    args = parser.parse_args()

    if args.command == 'interactive':
        interactive_tagging_session(args.csv_path)

    elif args.command == 'tag':
        search_engine = AudioTextSearch(args.csv_path)
        tags = args.tags.split(',') if args.tags else []
        tags = [tag.strip() for tag in tags if tag.strip()]
        search_engine.add_description(args.filename, args.description, tags)

    elif args.command == 'search':
        search_engine = AudioTextSearch(args.csv_path)
        results = search_engine.search_by_text(args.query)
        for result in results:
            print(f"{result['filename']}: {result['description']}")

    elif args.command == 'tags':
        search_engine = AudioTextSearch(args.csv_path)
        tags = [tag.strip() for tag in args.tags.split(',')]
        results = search_engine.search_by_tags(tags)
        for result in results:
            print(f"{result['filename']}: Tags={result['matching_tags']}")

    else:
        parser.print_help()