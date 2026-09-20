from math import sqrt
from random import Random


class RegimeResearchPipeline:
    """Deterministic, research-only k-means candidate builder. It never changes the production detector."""
    version = "research-kmeans-v1"
    def __init__(self, feature_names: tuple[str, ...] = ("adx_proxy_14", "volatility_percentile", "spread_percentile"), clusters: int = 4, seed: int = 7) -> None:
        self.feature_names, self.clusters, self.seed, self.centroids = feature_names, clusters, seed, []

    def fit(self, snapshots: list[dict], iterations: int = 20) -> dict:
        rows = [[float(row[name]) for name in self.feature_names] for row in snapshots if all(row.get(name) is not None for name in self.feature_names)]
        if len(rows) < self.clusters: raise ValueError("Insufficient complete feature snapshots for regime research.")
        rng = Random(self.seed); self.centroids = [row[:] for row in rng.sample(rows, self.clusters)]
        for _ in range(iterations):
            buckets = [[] for _ in self.centroids]
            for row in rows: buckets[self._nearest(row)].append(row)
            next_centroids = [([sum(column) / len(bucket) for column in zip(*bucket)] if bucket else self.centroids[index]) for index, bucket in enumerate(buckets)]
            if next_centroids == self.centroids: break
            self.centroids = next_centroids
        return {"version": self.version, "feature_names": self.feature_names, "centroids": self.centroids, "observations": len(rows), "research_only": True}

    def predict_cluster(self, features: dict) -> tuple[int, float]:
        if not self.centroids: raise RuntimeError("Research model has not been fitted.")
        row = [float(features[name]) for name in self.feature_names]; distances = [self._distance(row, center) for center in self.centroids]
        index = min(range(len(distances)), key=distances.__getitem__)
        confidence = 1 / (1 + distances[index])
        return index, confidence

    def _nearest(self, row: list[float]) -> int: return min(range(len(self.centroids)), key=lambda index: self._distance(row, self.centroids[index]))
    @staticmethod
    def _distance(left: list[float], right: list[float]) -> float: return sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
