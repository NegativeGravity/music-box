"""Independent genre-policy decider for a music recommendation pipeline.

The decider selects the genre for each recommendation slot. It does not
calculate track relevance or inspect any user's private audio beyond eligible
catalog metadata. The downstream ranker must enforce every selected genre.

Policy:
- Use the primary liked genre for 1 - exploration_rate of available slots.
- Explore only nearest eligible genres for exploration_rate of slots.
- Temperature only changes WHICH neighboring genre is sampled, not the
  overall exploration rate.
- Never silently fall back to unrelated genres when candidates run out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


CATEGORY_ALIASES: dict[str, str] = {
    'rap': 'Hip-Hop',
    'hiphop': 'Hip-Hop',
    'hip-hop': 'Hip-Hop',
    'رپ': 'Hip-Hop',
    'هیپ هاپ': 'Hip-Hop',
}


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise ValueError('Embeddings must be a finite, 2D matrix.')
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms < 1e-10).any():
        raise ValueError('Embeddings include a zero-length vector.')
    return matrix / norms


@dataclass(frozen=True)
class CategoryPolicy:
    exploration_rate: float = 0.20
    temperature: float = 0.15
    max_neighbors: int = 3
    min_similarity: float = 0.0
    seed: int = 42

    def __post_init__(self) -> None:
        if not 0.0 <= self.exploration_rate <= 1.0:
            raise ValueError('exploration_rate must be between zero and one.')
        if not np.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError('temperature must be strictly positive and finite.')
        if self.max_neighbors < 1:
            raise ValueError('max_neighbors must be at least one.')
        if not np.isfinite(self.min_similarity) or not -1 <= self.min_similarity <= 1:
            raise ValueError('min_similarity must be a cosine value in [-1, 1].')


@dataclass(frozen=True)
class CategoryDecision:
    slot: int
    category: str
    mode: str  # exploit | explore
    primary_category: str
    affinity: float


@dataclass(frozen=True)
class CategoryPlan:
    primary_category: str
    decisions: tuple[CategoryDecision, ...]
    neighbor_probabilities: Mapping[str, float]
    target_exploration_rate: float

    @property
    def realized_exploration_rate(self) -> float:
        if not self.decisions:
            return 0.0
        return sum(item.category != self.primary_category for item in self.decisions) / len(self.decisions)

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame([vars(item) for item in self.decisions],
                            columns=['slot', 'category', 'mode', 'primary_category', 'affinity'])


class CategoryDecider:
    """Genre similarity model: global-centered, L2-normalized genre prototypes.

    This is a baseline *hypothesis*, not an externally validated genre taxonomy.
    Centering is important for anisotropic CLAP embeddings, but neighbor quality
    still requires human-labeled evaluation on a disjoint audio catalog.
    """

    def __init__(self, catalog: pd.DataFrame, audio_embeddings: np.ndarray,
                 aliases: Mapping[str, str] | None = None):
        if not {'track_id', 'genre'}.issubset(catalog.columns):
            raise ValueError('Catalog requires track_id and genre columns.')
        if catalog['track_id'].duplicated().any() or catalog['genre'].isna().any():
            raise ValueError('Catalog contains duplicate IDs or missing genres.')
        if len(catalog) != len(audio_embeddings) or catalog.empty:
            raise ValueError('Catalog and audio embeddings must be row-aligned and nonempty.')
        self.catalog = catalog.reset_index(drop=True)
        self.genres = tuple(sorted(str(g) for g in self.catalog.genre.unique()))
        self.aliases = {**CATEGORY_ALIASES, **(aliases or {})}
        normalized = _normalize_rows(audio_embeddings)
        global_mean = normalized.mean(axis=0)
        # Center category means globally instead of normalizing individual tracks
        # after subtracting the mean; this preserves centroid interpretation.
        means = np.stack([normalized[self.catalog.genre.eq(genre).to_numpy()].mean(axis=0)
                          for genre in self.genres])
        centered = means - global_mean
        lengths = np.linalg.norm(centered, axis=1)
        if (lengths < 1e-10).any():
            # This happens in a single-genre catalog and some degenerate test fixtures.
            # Exploration is then disabled; no ungrounded neighbors are invented.
            self.similarity = np.eye(len(self.genres), dtype=np.float64)
        else:
            prototypes = centered / lengths[:, None]
            self.similarity = np.clip(prototypes @ prototypes.T, -1, 1)
        self._genre_index = {name: idx for idx, name in enumerate(self.genres)}
        self._track_genre = dict(zip(self.catalog.track_id.astype(int), self.catalog.genre.astype(str)))

    def resolve_category(self, category: str) -> str:
        clean = str(category).strip()
        canonical = next((g for g in self.genres if g.casefold() == clean.casefold()), None)
        if canonical:
            return canonical
        mapped = self.aliases.get(clean.casefold())
        if mapped in self._genre_index:
            return mapped
        raise ValueError(f'Category {category!r} is not in this catalog: {self.genres}')

    def infer_primary(self, positive_ids: Sequence[int], explicit_category: str | None = None,
                      session_ids: Sequence[int] = ()) -> str:
        """Explicit genre overrides history; historical counts are a cold-start baseline.

        DO NOT use held-out future feedback here. Recency and user-specific weights
        can replace the uniform vote once trustworthy behavioral events exist.
        """
        if explicit_category is not None:
            return self.resolve_category(explicit_category)
        for source in (positive_ids, session_ids):
            counts: dict[str, int] = {}
            for item in dict.fromkeys(map(int, source)):
                if item not in self._track_genre:
                    raise ValueError(f'Unknown preference track ID: {item}')
                g = self._track_genre[item]
                counts[g] = counts.get(g, 0) + 1
            if counts:
                return sorted(counts, key=lambda g: (-counts[g], g))[0]
        raise ValueError('No genre preference exists; ask for onboarding choices.')

    def neighbor_scores(self, primary_category: str) -> dict[str, float]:
        primary = self.resolve_category(primary_category)
        source = self._genre_index[primary]
        return {name: float(self.similarity[source, index])
                for index, name in enumerate(self.genres) if name != primary}

    def neighbor_distribution(self, primary_category: str, policy: CategoryPolicy,
                              capacity: Mapping[str, int] | None = None) -> dict[str, float]:
        similarities = self.neighbor_scores(primary_category)
        ranked = sorted(((g, s) for g, s in similarities.items()
                         if s >= policy.min_similarity and (capacity is None or capacity.get(g, 0) > 0)),
                        key=lambda item: (-item[1], item[0]))[:policy.max_neighbors]
        if not ranked:
            return {}
        logit = np.array([score for _, score in ranked], dtype=np.float64) / policy.temperature
        prob = np.exp(logit - logit.max())
        prob /= prob.sum()
        return {genre: float(p) for (genre, _), p in zip(ranked, prob)}

    def decide(self, primary_category: str, k: int, policy: CategoryPolicy | None = None,
               eligible_track_ids: Sequence[int] | None = None,
               excluded_track_ids: Sequence[int] = (),
               blocked_artist_ids: Sequence[str] = (),
               max_per_artist: int | None = None) -> CategoryPlan:
        """Plan categories BEFORE track scoring; capacities honor pre-rank eligibility.

        A shortage cannot be concealed: plan may contain fewer than k slots.
        `eligible_track_ids` should already be restricted to playable, licensed
        tracks for this user by the service layer.
        """
        cfg = policy or CategoryPolicy()
        if k < 1:
            raise ValueError('k must be positive.')
        if max_per_artist is not None and max_per_artist < 1:
            raise ValueError('max_per_artist must be positive.')
        anchor = self.resolve_category(primary_category)
        subset = self.catalog
        if eligible_track_ids is not None:
            allowed = set(map(int, eligible_track_ids))
            subset = subset.loc[subset.track_id.isin(allowed)]
        excluded = set(map(int, excluded_track_ids))
        subset = subset.loc[~subset.track_id.isin(excluded)]
        if blocked_artist_ids:
            if 'artist_id' not in subset:
                raise ValueError('blocked_artist_ids requires the artist_id column.')
            subset = subset.loc[~subset.artist_id.astype(str).isin(set(map(str, blocked_artist_ids)))]
        if max_per_artist is None:
            capacity = subset.genre.value_counts().astype(int).to_dict()
        else:
            if 'artist_id' not in subset:
                raise ValueError('max_per_artist requires the artist_id column.')
            artist_limits = (subset.groupby(['genre', 'artist_id']).size()
                             .clip(upper=max_per_artist).groupby(level=0).sum())
            capacity = artist_limits.astype(int).to_dict()
        available = {genre: int(capacity.get(genre, 0)) for genre in self.genres}
        rng = np.random.default_rng(cfg.seed)
        distribution = self.neighbor_distribution(anchor, cfg, available)
        neighbor_capacity = sum(available[g] for g in distribution)
        expected = k * cfg.exploration_rate
        exploration = int(np.floor(expected) + (rng.random() < (expected % 1)))
        # Capacity-aware rescaling. An exhausted anchor is NOT secretly filled by
        # arbitrary genres; only pre-qualified adjacent genres are considered.
        anchor_count = min(k - exploration, available[anchor])
        neighbor_count = min(exploration, neighbor_capacity)
        # When close genres are exhausted, fill with more primary-genre tracks.
        # When the primary is exhausted, never exceed the exploration budget
        # merely to fill k: return fewer than k rather than drift off-policy.
        anchor_count = min(k - neighbor_count, available[anchor])
        chosen_modes = ['exploit'] * anchor_count + ['explore'] * neighbor_count
        rng.shuffle(chosen_modes)
        decisions = []
        for mode in chosen_modes:
            if mode == 'exploit':
                genre = anchor
            else:
                remaining = {g: p for g, p in distribution.items() if available[g] > 0}
                if not remaining:
                    raise AssertionError('Neighbor capacities were inconsistent.')
                cats, weights = list(remaining), np.fromiter(remaining.values(), dtype=float)
                weights /= weights.sum()
                genre = str(rng.choice(cats, p=weights))
            available[genre] -= 1
            affinity = 1.0 if genre == anchor else self.neighbor_scores(anchor)[genre]
            decisions.append(CategoryDecision(slot=len(decisions) + 1, category=genre,
                                              mode=mode if genre == anchor else 'explore',
                                              primary_category=anchor, affinity=affinity))
        return CategoryPlan(primary_category=anchor, decisions=tuple(decisions),
                            neighbor_probabilities=distribution,
                            target_exploration_rate=cfg.exploration_rate)
