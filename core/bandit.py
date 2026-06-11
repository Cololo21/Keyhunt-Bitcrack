"""Bandit jerárquico (Thompson Sampling + UCB)"""

import random
import math
import json
import threading
import os

import config
from monitoring.logs import add_log


class BanditRegion:
    __slots__ = (
        "alpha", "beta", "visits",
        "last_fitness", "last_visit_tick",
        "gradient", "autocorr"
    )

    def __init__(self, alpha: float, beta: float):
        self.alpha = alpha
        self.beta = beta
        self.visits = 0
        self.last_fitness = 0.0
        self.last_visit_tick = 0
        self.gradient = 0.0
        self.autocorr = 0.0

    def sample_thompson(self):
        return random.betavariate(self.alpha, self.beta)

    def ucb_score(self, total_visits: int, c: float):
        if self.visits == 0:
            return float("inf")
        mean = self.alpha / (self.alpha + self.beta)
        return mean + c * math.sqrt(math.log(max(total_visits, 1)) / self.visits)

    def combined_score(self, total_visits: int, c: float):
        return self.sample_thompson() + 0.3 * self.ucb_score(total_visits, c)

    def update(self, fitness_norm: float, tick: int):
        self.alpha += fitness_norm
        self.beta += max(0.01, 1.0 - fitness_norm)
        self.visits += 1
        self.last_fitness = fitness_norm
        self.last_visit_tick = tick

    def decay(self, tick: int, decay_rate: float):
        idle = tick - self.last_visit_tick
        if idle > 100:
            factor = decay_rate ** (idle - 100)
            excess_a = (self.alpha - config.BANDIT_PRIOR_A) * factor
            excess_b = (self.beta - config.BANDIT_PRIOR_B) * factor
            self.alpha = config.BANDIT_PRIOR_A + max(0.0, excess_a)
            self.beta = config.BANDIT_PRIOR_B + max(0.0, excess_b)


class HierarchicalBandit:

    def __init__(self):
        self.tick = 0
        self.lock = threading.Lock()

        self.macro = [
            BanditRegion(config.BANDIT_PRIOR_A, config.BANDIT_PRIOR_B)
            for _ in range(config.BANDIT_MACRO)
        ]

        self.total_visits = 0
        self.topk = []

        self._apply_puzzle_prior()
        self._load_state()

    def _apply_puzzle_prior(self):
        known_pcts = [
            82.86, 66.79, 82.17, 95.01, 79.78, 79.78,
            25.3, 28.87, 51.49, 63.98, 70.29, 68.48, 70.06
        ]

        for pct in known_pcts:
            idx = min(int(pct), config.BANDIT_MACRO - 1)
            self.macro[idx].alpha += 1.5

            for d in (-2, -1, 1, 2):
                j = idx + d
                if 0 <= j < config.BANDIT_MACRO:
                    self.macro[j].alpha += 0.4

    def _load_state(self):
        if not os.path.exists(config.BANDIT_STATE_FILE):
            return

        try:
            with open(config.BANDIT_STATE_FILE) as f:
                state = json.load(f)

            self.tick = state.get("tick", 0)
            self.total_visits = state.get("total_visits", 0)
            self.topk = [tuple(x) for x in state.get("topk", [])]

            for i, ms in enumerate(state.get("macro", [])):
                if i >= config.BANDIT_MACRO:
                    break
                r = self.macro[i]
                r.alpha = ms.get("a", r.alpha)
                r.beta = ms.get("b", r.beta)
                r.visits = ms.get("v", 0)
                r.last_visit_tick = ms.get("t", 0)

            add_log(f"Bandit cargado (tick={self.tick})", "INFO")

        except Exception as e:
            add_log(f"Bandit load error: {e}", "WARN")

    def save_state(self):
        try:
            state = {
                "tick": self.tick,
                "total_visits": self.total_visits,
                "topk": list(self.topk),
                "macro": [
                    {
                        "a": r.alpha,
                        "b": r.beta,
                        "v": r.visits,
                        "t": r.last_visit_tick
                    }
                    for r in self.macro
                ]
            }

            tmp = config.BANDIT_STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)

            os.replace(tmp, config.BANDIT_STATE_FILE)

        except Exception as e:
            add_log(f"Bandit save error: {e}", "WARN")

    def _macro_for_slice(self, slice_num: int) -> int:
        return min(
            int(slice_num / config.SLICE_COUNT * config.BANDIT_MACRO),
            config.BANDIT_MACRO - 1
        )

    def update_from_fitness(self, slice_num: int, fitness_raw: int, key_int: int):
        MAX_FITNESS = len(config.ADDRESS) * 4
        fitness_norm = min(1.0, fitness_raw / MAX_FITNESS)

        with self.lock:
            self.tick += 1

            idx = self._macro_for_slice(slice_num)
            self.macro[idx].update(fitness_norm, self.tick)

            self.total_visits += 1

            self.topk.append((fitness_norm, key_int, hex(key_int)))
            self.topk.sort(key=lambda x: x[0], reverse=True)

            if len(self.topk) > config.BANDIT_TOPK:
                self.topk = self.topk[:config.BANDIT_TOPK]

    def get_heatmap(self):
        with self.lock:
            return [
                {
                    "idx": i,
                    "alpha": round(r.alpha, 3),
                    "beta": round(r.beta, 3),
                    "visits": r.visits,
                    "mean": round(r.alpha / (r.alpha + r.beta), 4),
                }
                for i, r in enumerate(self.macro)
            ]

    def get_top_regions(self, n: int = 10) -> list:
        """Retorna las top n regiones por valor medio"""
        with self.lock:
            regions = []
            for i, r in enumerate(self.macro):
                mean = r.alpha / (r.alpha + r.beta) if (r.alpha + r.beta) > 0 else 0
                regions.append({
                    "region": i,
                    "value": round(mean, 4),
                    "count": r.visits,
                    "alpha": round(r.alpha, 3),
                    "beta": round(r.beta, 3)
                })
            
            # Ordenar por valor medio (mean) descendente
            regions.sort(key=lambda x: x["value"], reverse=True)
            return regions[:n]

    def get_best_slice(self, completed_set: set = None) -> int:
        """Retorna el mejor slice según el bandit"""
        from config import SLICE_COUNT
        
        with self.lock:
            # Calcular scores para cada región
            scores = []
            for i, r in enumerate(self.macro):
                mean = r.alpha / (r.alpha + r.beta) if (r.alpha + r.beta) > 0 else 0
                scores.append((i, mean))
            
            # Ordenar por score
            scores.sort(key=lambda x: x[1], reverse=True)
            
            # Seleccionar un slice dentro de la mejor región
            if scores:
                best_region = scores[0][0]
                # Calcular rango de slices para esa región
                region_size = SLICE_COUNT // config.BANDIT_MACRO
                start = best_region * region_size
                end = min(start + region_size, SLICE_COUNT)
                
                # Buscar slice no completado en esa región
                if completed_set:
                    for s in range(start, end):
                        if s not in completed_set:
                            return s
                
                # Si todos están completados, devolver None
                return None
            return None


# =========================
# GLOBAL INSTANCE
# =========================
bandit = HierarchicalBandit()