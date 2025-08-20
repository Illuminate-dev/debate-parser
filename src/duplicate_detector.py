import hashlib
from typing import Optional

from datasketch import MinHash, MinHashLSH


class DuplicateDetector:
    """Optimized duplicate detection"""

    def __init__(self, shingle_size=3) -> None:
        self.shingle_size = shingle_size
        self.threshold = 0.85
        self.lsh = MinHashLSH(threshold=self.threshold, num_perm=128)
        self.detected_duplicates = []
        self.content_to_id = {}
        self.id_counter = 0

    def check(self, s: str, card_info: Optional[dict] = None) -> bool:
        """Returns whether the card is a duplicate and records duplicate info"""
        shingle_set = self._get_shingles(s)
        query = MinHash(num_perm=128)
        for d in shingle_set:
            query.update(d.encode("utf8"))

        results = self.lsh.query(query)
        is_duplicate = len(results) > 0

        if is_duplicate and card_info:
            original_id = results[0] if results else "unknown"
            duplicate_info = {
                "duplicate_card": card_info,
                "original_content_id": original_id,
                "similarity_score": self.threshold,
                "content_hash": s[:100] + "..." if len(s) > 100 else s,
            }
            self.detected_duplicates.append(duplicate_info)

        return is_duplicate

    def add(self, s: str, content_id: Optional[str] = None) -> None:
        """Adds a string to the duplicate detector"""
        if s in self.content_to_id:
            return

        shingle_set = self._get_shingles(s)
        m = MinHash(num_perm=128)
        for d in shingle_set:
            m.update(d.encode("utf8"))

        if content_id is None:
            content_id = f"content_{self.id_counter}"
        else:
            content_id = f"{content_id}_{self.id_counter}"

        self.id_counter += 1
        self.content_to_id[s] = content_id
        self.lsh.insert(content_id, m)

    def _get_shingles(self, s: str) -> set:
        """Returns a set of the shingles for a certain string"""
        tokens = s.split()
        size = self.shingle_size
        return set(
            [" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)]
        )


class FastDuplicateDetector:
    def __init__(self):
        self.exact_hashes = set()
        self.detected_duplicates = []

    def check(self, content: str, card_info: Optional[dict] = None) -> bool:
        """Check for exact duplicates using SHA256 hash"""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        is_duplicate = content_hash in self.exact_hashes

        if is_duplicate and card_info:
            duplicate_info = {
                "duplicate_card": card_info,
                "original_content_hash": content_hash,
                "similarity_score": 1.0,  # Exact match
                "detection_method": "exact_hash",
            }
            self.detected_duplicates.append(duplicate_info)

        return is_duplicate

    def add(self, content: str, _content_id: Optional[str] = None) -> None:
        """Add content hash to the set of seen content"""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.exact_hashes.add(content_hash)
