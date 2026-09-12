#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monte Carlo Lift Traffic Analysis Script
Rev 67 - Absolute Path Config & Cleaned Parameters
"""

import collections
import itertools
import math
import os
import random
import numpy as np
import pandas as pd
import simpy

# =============================================================================
# 0. EXCEL DOSYASINDAN PARAMETRELERİN VE BİNA YAPISININ OKUNMASI
# =============================================================================


def load_simulation_config(
    file_path="config.xlsx",
):
    """Excel dosyasından bina yapılandırmasını ve simülasyon sabitlerini okur."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Yapılandırma dosyası bulunamadı: '{file_path}'. Lütfen dosyanın mevcut olduğunu kontrol edin."
        )

    # 1. Bina Kat Yapılandırmasını Oku
    df_floors = pd.read_excel(file_path, sheet_name="Building_Config")
    expected_cols = {"floor", "guest", "height"}
    if not expected_cols.issubset(set(df_floors.columns)):
        raise ValueError(
            f"'Building_Config' sayfası şu kolonları içermelidir: {expected_cols}"
        )

    floor_config = {}
    for _, row in df_floors.iterrows():
        f_num = int(row["floor"])
        floor_config[f_num] = {
            "guests": int(row["guest"]),
            "height": float(row["height"]),
        }

    # 2. Simülasyon Sabitlerini Oku
    df_params = pd.read_excel(file_path, sheet_name="Simulation_Params")
    params = dict(zip(df_params["Parameter"], df_params["Value"]))

    num_sim_runs = int(params["NUM_SIMULATION_RUNS"])
    num_lifts = int(params["NUM_LIFTS"])
    capacity = int(params["CAPACITY"])
    lift_max_speed = float(params["LIFT_MAX_SPEED"])

    # 3. Hıza Bağlı İvme (LIFT_ACCELERATION) Koşullu Ataması
    if lift_max_speed < 1.0:
        lift_acceleration = 0.6
    elif 1.0 <= lift_max_speed < 2.0:
        lift_acceleration = 0.8
    elif 2.0 <= lift_max_speed < 6.0:
        lift_acceleration = 1.0
    else:
        lift_acceleration = 1.2

    return {
        "FLOOR_CONFIG": floor_config,
        "NUM_SIMULATION_RUNS": num_sim_runs,
        "NUM_LIFTS": num_lifts,
        "CAPACITY": capacity,
        "LIFT_MAX_SPEED": lift_max_speed,
        "LIFT_ACCELERATION": lift_acceleration,
    }


# Excel dosyasından parametreleri yükle
CONFIG = load_simulation_config()

FLOOR_CONFIG = CONFIG["FLOOR_CONFIG"]
NUM_SIMULATION_RUNS = CONFIG["NUM_SIMULATION_RUNS"]
NUM_LIFTS = CONFIG["NUM_LIFTS"]
CAPACITY = CONFIG["CAPACITY"]
LIFT_MAX_SPEED = CONFIG["LIFT_MAX_SPEED"]
LIFT_ACCELERATION = CONFIG["LIFT_ACCELERATION"]

# Konfigürasyondan Türetilen Dinamik Sözlükler ve Sabitler
GUESTS_PER_FLOOR = {f: cfg["guests"] for f, cfg in FLOOR_CONFIG.items()}
FLOOR_HEIGHTS = {f: cfg["height"] for f, cfg in FLOOR_CONFIG.items()}

NUM_FLOORS = len(FLOOR_CONFIG)
BOARD_TIME = 1.0
DISEMBARK_TIME = 1.0
DOOR_OPEN_CLOSE_TIME = 4.5
SIMULATION_DURATION = 7200  # 7200 Saniye (2 Saat)
VISUALIZATION_UPDATE_INTERVAL = 0.1
ARRIVAL_RATE_FACTOR = 1

MAX_PASSENGERS_IN_BUILDING = sum(GUESTS_PER_FLOOR.values())

print(
    f"[BILGI] Hız: {LIFT_MAX_SPEED} m/s -> Hesaplanan İvme: {LIFT_ACCELERATION} m/s²"
)


def get_distance_meters(f1, f2):
    """İki kat arasındaki fiziksel yüksekliği (m) kat bazlı yüksekliklerden hesaplar."""
    f_min, f_max = int(min(f1, f2)), int(max(f1, f2))
    return sum(FLOOR_HEIGHTS.get(f, 3.3) for f in range(f_min, f_max))


class TripleTripGuestTracker:

    def __init__(self, max_guests, guests_per_floor):
        self.max_guests = max_guests
        self.guests_per_floor = guests_per_floor
        self.active_floors = [f for f, g_cnt in guests_per_floor.items() if g_cnt > 0]
        floor_weights = [guests_per_floor[f] for f in self.active_floors]

        self.guest_home_rooms = {
            i: random.choices(self.active_floors, weights=floor_weights, k=1)[0]
            for i in range(max_guests)
        }

        guest_ids = list(range(max_guests))
        random.shuffle(guest_ids)

        self.guest_current_locations = {}
        self.guest_states = {}
        self.trip_counts = {g_id: 0 for g_id in guest_ids}
        self.max_trips_per_guest = 3

        for g_id in guest_ids:
            self.guest_states[g_id] = "IN_ROOM"
            self.guest_current_locations[g_id] = self.guest_home_rooms[g_id]

        self.next_available_time = {}
        for g_id in guest_ids:
            self.next_available_time[g_id] = random.uniform(10, 3600)

    def get_available_guest_for_trip(self, current_time):
        for g_id, state in list(self.guest_states.items()):
            if self.trip_counts[g_id] < self.max_trips_per_guest and current_time >= self.next_available_time.get(
                g_id, 999999
            ):
                if state == "IN_ROOM":
                    arr_f = self.guest_current_locations[g_id]
                    self.guest_states[g_id] = "TRANSIT"
                    self.trip_counts[g_id] += 1
                    return g_id, arr_f, 0
                elif state == "IN_LOBBY":
                    self.guest_states[g_id] = "TRANSIT"
                    self.trip_counts[g_id] += 1
                    return g_id, 0, self.guest_home_rooms[g_id]
        return None, None, None

    def update_guest_state_on_arrival(self, guest_id, dest_floor, current_time):
        if guest_id is not None:
            self.guest_current_locations[guest_id] = dest_floor
            if dest_floor == 0:
                self.guest_states[guest_id] = "IN_LOBBY"
                if self.trip_counts[guest_id] < self.max_trips_per_guest:
                    self.next_available_time[guest_id] = current_time + random.uniform(
                        1800, 2400
                    ) * ARRIVAL_RATE_FACTOR
                else:
                    self.next_available_time[guest_id] = 9999999
            elif dest_floor == self.guest_home_rooms[guest_id]:
                self.guest_states[guest_id] = "IN_ROOM"
                self.next_available_time[guest_id] = current_time + random.uniform(
                    900, 1800
                ) * ARRIVAL_RATE_FACTOR


class Passenger:

    def __init__(
        self, env, passenger_id, arrival_floor, destination_floor, guest_id=None
    ):
        self.env = env
        self.id = passenger_id
        self.guest_id = guest_id
        self.arrival_floor = arrival_floor
        self.destination_floor = destination_floor
        self.board_time = -1
        self.disembark_time = -1
        self.wait_time_start = self.env.now
        self.wait_time = 0
        self.trip_time = 0
        self.total_time = 0
        self.direction = "up" if destination_floor > arrival_floor else "down"


class BuildingController:

    def __init__(self, env, num_floors, guest_tracker):
        self.env = env
        self.num_floors = num_floors
        self.guest_tracker = guest_tracker

        self.floor_call_queues = {
            floor: {"up": [], "down": []} for floor in range(1, self.num_floors)
        }
        self.passengers_in_lobby = []
        self.lifts = []
        self.assigned_calls = {}
        self.all_passengers = []
        self.active_passengers = set()
        self.building_state_log = []
        self.lobby_landing_times = []

    def dispatch_calls(self):
        for f, d in list(self.assigned_calls.keys()):
            q = (
                self.passengers_in_lobby
                if f == 0
                else self.floor_call_queues[f][d]
            )
            if len(q) == 0:
                del self.assigned_calls[(f, d)]

        all_pending_calls = []

        if len(self.passengers_in_lobby) > 0:
            oldest = min(p.wait_time_start for p in self.passengers_in_lobby)
            all_pending_calls.append((0, "up", oldest))

        for f in range(1, self.num_floors):
            for d in ["up", "down"]:
                if len(self.floor_call_queues[f][d]) > 0:
                    oldest = min(
                        p.wait_time_start for p in self.floor_call_queues[f][d]
                    )
                    all_pending_calls.append((f, d, oldest))

        all_pending_calls.sort(key=lambda x: x[2])

        for f, d, oldest_time in all_pending_calls:
            current_assigned_lift_id = self.assigned_calls.get((f, d), None)
            best_lift = self.find_best_lift_for_call_eta(
                f, d, current_assigned_lift_id
            )
            if best_lift is not None:
                if current_assigned_lift_id != best_lift.id:
                    self.assigned_calls[(f, d)] = best_lift.id
                    best_lift.interrupt_idle()

    def find_best_lift_for_call_eta(
        self, floor, direction, current_assigned_lift_id
    ):
        best_lift = None
        min_cost = float("inf")

        if floor == 0:
            queue = self.passengers_in_lobby
        else:
            queue = self.floor_call_queues[floor][direction]

        if not queue:
            return None

        oldest_wait_start = min(p.wait_time_start for p in queue)
        wait_duration = max(0, self.env.now - oldest_wait_start)

        for lift in self.lifts:
            assigned_count = sum(
                (
                    len(self.passengers_in_lobby)
                    if af == 0
                    else len(self.floor_call_queues[af][ad])
                )
                for (af, ad), lid in self.assigned_calls.items()
                if lid == lift.id
            )
            total_projected_load = len(lift.passengers_onboard) + assigned_count

            if total_projected_load >= lift.capacity:
                continue

            distance_m = get_distance_meters(lift.current_floor, floor)

            t_acc = LIFT_MAX_SPEED / LIFT_ACCELERATION
            d_acc = 0.5 * LIFT_ACCELERATION * (t_acc**2)

            if distance_m >= 2 * d_acc:
                travel_time = (2 * t_acc) + (
                    (distance_m - (2 * d_acc)) / LIFT_MAX_SPEED
                )
            else:
                travel_time = 2 * math.sqrt(distance_m / LIFT_ACCELERATION)

            intermediate_stops = 0
            if lift.direction == "up" and floor > lift.current_floor:
                intermediate_stops += sum(
                    1
                    for p in lift.passengers_onboard
                    if lift.current_floor < p.destination_floor < floor
                )
            elif lift.direction == "down" and floor < lift.current_floor:
                intermediate_stops += sum(
                    1
                    for p in lift.passengers_onboard
                    if lift.current_floor > p.destination_floor > floor
                )

            stop_penalty = intermediate_stops * (DOOR_OPEN_CLOSE_TIME + 2.0)

            direction_penalty = 0.0
            if lift.direction is not None and len(lift.passengers_onboard) > 0:
                if (
                    lift.direction == "up" and floor < lift.current_floor
                ) or (lift.direction == "down" and floor > lift.current_floor):
                    direction_penalty = 25.0
                elif lift.direction != direction:
                    direction_penalty = 12.0

            coincidence_bonus = 0.0
            if any(
                p.destination_floor == floor for p in lift.passengers_onboard
            ):
                coincidence_bonus = 8.0

            eta = (
                travel_time
                + stop_penalty
                + direction_penalty
                - coincidence_bonus
            )

            aging_factor = (wait_duration**2.2) * 0.15
            cost = eta - aging_factor + (total_projected_load * 2.5)

            if current_assigned_lift_id == lift.id:
                cost -= 5.0

            if cost < min_cost:
                min_cost = cost
                best_lift = lift

        return best_lift

    def add_passenger_call(self, passenger):
        self.all_passengers.append(passenger)
        self.active_passengers.add(passenger.id)
        if passenger.arrival_floor == 0:
            self.passengers_in_lobby.append(passenger)
        else:
            direction = (
                "up"
                if passenger.destination_floor > passenger.arrival_floor
                else "down"
            )
            self.floor_call_queues[passenger.arrival_floor][direction].append(
                passenger
            )

        self.dispatch_calls()

    def remove_served_passenger(self, passenger):
        if passenger.id in self.active_passengers:
            self.active_passengers.remove(passenger.id)
        self.guest_tracker.update_guest_state_on_arrival(
            passenger.guest_id, passenger.destination_floor, self.env.now
        )

    def record_lobby_landing(self, time):
        self.lobby_landing_times.append(time)


class Lift:

    def __init__(self, env, id, building_controller, capacity, num_floors):
        self.env = env
        self.id = id
        self.building_controller = building_controller
        self.capacity = capacity
        self.num_floors = num_floors
        self.current_floor = 0.0
        self.direction = None
        self.passengers_onboard = []
        self.destination_floor = None
        self.trip_count = 0
        self.lift_movement_log = []
        self.total_travel_time = 0
        self.idle_event = simpy.Event(env)

        self.home_floor = 0 if id == 0 else 13
        self.action = env.process(self.run())

    def interrupt_idle(self):
        if not self.idle_event.triggered:
            self.idle_event.succeed()
            self.idle_event = simpy.Event(self.env)

    def run(self):
        while True:
            self.building_controller.dispatch_calls()
            my_assigned_floors = [
                f
                for (f, d), lid in self.building_controller.assigned_calls.items()
                if lid == self.id
            ]
            car_dests = [p.destination_floor for p in self.passengers_onboard]

            while not my_assigned_floors and not car_dests:
                if self.current_floor != self.home_floor:
                    self.destination_floor = self.home_floor
                    self.direction = (
                        "up"
                        if self.home_floor > self.current_floor
                        else "down"
                    )
                    yield self.env.process(self._move_to_floor())
                    my_assigned_floors = [
                        f
                        for (
                            f,
                            d,
                        ), lid in self.building_controller.assigned_calls.items()
                        if lid == self.id
                    ]
                    car_dests = [
                        p.destination_floor for p in self.passengers_onboard
                    ]
                    continue
                else:
                    self.direction = None
                    self.destination_floor = None
                    try:
                        yield self.idle_event
                    except simpy.Interrupt:
                        pass
                    yield self.env.timeout(0.2 + (self.id * 0.2))
                    self.building_controller.dispatch_calls()
                    my_assigned_floors = [
                        f
                        for (
                            f,
                            d,
                        ), lid in self.building_controller.assigned_calls.items()
                        if lid == self.id
                    ]
                    car_dests = [
                        p.destination_floor for p in self.passengers_onboard
                    ]

            target = self._get_my_next_destination(
                my_assigned_floors, car_dests
            )
            if target is None:
                yield self.env.timeout(0.5)
                continue

            self.destination_floor = target

            while self.current_floor != self.destination_floor:
                yield self.env.process(self._move_to_floor())

            yield self.env.process(self._stop_at_floor())

    def _get_my_next_destination(self, assigned_floors, car_dests):
        assigned_floors = [
            f
            for (f, d), lid in self.building_controller.assigned_calls.items()
            if lid == self.id
        ]
        all_targets = list(set(assigned_floors + car_dests))
        if not all_targets:
            return None

        curr_f = int(self.current_floor)

        if self.direction == "up":
            above = [f for f in all_targets if f > curr_f]
            if above:
                return min(above)
            else:
                self.direction = "down"
                below = [f for f in all_targets if f < curr_f]
                return max(below) if below else None

        elif self.direction == "down":
            below = [f for f in all_targets if f < curr_f]
            if below:
                return max(below)
            above = [f for f in all_targets if f > curr_f]
            if above:
                self.direction = "up"
                return min(above)
            return None
        else:
            target = min(all_targets, key=lambda f: abs(f - curr_f))
            self.direction = (
                "up" if target > curr_f else ("down" if target < curr_f else "up")
            )
            return target

    def _move_to_floor(self):
        curr_f_int = int(self.current_floor)
        step = 1 if self.destination_floor > self.current_floor else -1
        h = FLOOR_HEIGHTS.get(curr_f_int if step == 1 else curr_f_int - 1, 3.3)

        start_f = self.current_floor

        t_acc = LIFT_MAX_SPEED / LIFT_ACCELERATION
        d_acc = 0.5 * LIFT_ACCELERATION * (t_acc**2)

        if h >= 2 * d_acc:
            travel_duration = (2 * t_acc) + ((h - (2 * d_acc)) / LIFT_MAX_SPEED)
        else:
            travel_duration = 2 * math.sqrt(h / LIFT_ACCELERATION)

        yield self.env.timeout(travel_duration)
        self.current_floor += step
        self.total_travel_time += travel_duration

        self.lift_movement_log.append({
            "time": self.env.now,
            "lift_id": self.id,
            "event": "moved_to_floor",
            "from_floor": int(start_f),
            "to_floor": int(self.current_floor),
            "travel_duration": round(travel_duration, 2),
        })

    def _stop_at_floor(self):
        curr_f = int(self.current_floor)
        if curr_f == 0:
            self.building_controller.record_lobby_landing(self.env.now)

        ado_bonus = 1.5
        door_open_time = max(0.2, (DOOR_OPEN_CLOSE_TIME / 2) - ado_bonus)

        yield self.env.timeout(door_open_time)
        yield self.env.process(self._disembark_passengers())
        yield self.env.process(self._board_passengers())
        yield self.env.timeout(DOOR_OPEN_CLOSE_TIME / 2)

    def _disembark_passengers(self):
        passengers_to_remove = []
        ids = []
        curr_f = int(self.current_floor)
        for p in self.passengers_onboard:
            if p.destination_floor == curr_f:
                p.disembark_time = self.env.now
                p.trip_time = p.disembark_time - p.board_time
                p.total_time = p.disembark_time - p.wait_time_start
                passengers_to_remove.append(p)
                ids.append(p.id)

        if ids:
            self.lift_movement_log.append({
                "time": self.env.now,
                "lift_id": self.id,
                "event": "disembark",
                "floor": curr_f,
                "passengers_ids": ids,
                "count": len(ids),
            })
            yield self.env.timeout(DISEMBARK_TIME * len(ids))

        for p in passengers_to_remove:
            self.passengers_onboard.remove(p)
            self.building_controller.remove_served_passenger(p)

    def _board_passengers(self):
        boarded_ids = []
        to_board = []
        curr_f = int(self.current_floor)

        if curr_f == 0:
            queue = self.building_controller.passengers_in_lobby
            q_dir = "up"
        else:
            up_q = self.building_controller.floor_call_queues[curr_f]["up"]
            down_q = self.building_controller.floor_call_queues[curr_f]["down"]

            if len(self.passengers_onboard) > 0:
                queue = up_q if self.direction == "up" else down_q
                q_dir = self.direction
            else:
                if up_q and down_q:
                    oldest_up = min(p.wait_time_start for p in up_q)
                    oldest_down = min(p.wait_time_start for p in down_q)
                    if oldest_up <= oldest_down:
                        queue, q_dir = up_q, "up"
                    else:
                        queue, q_dir = down_q, "down"
                elif up_q:
                    queue, q_dir = up_q, "up"
                elif down_q:
                    queue, q_dir = down_q, "down"
                else:
                    queue, q_dir = [], self.direction

        self.direction = q_dir
        sorted_queue = sorted(list(queue), key=lambda p: p.wait_time_start)

        for p in sorted_queue:
            if len(self.passengers_onboard) + len(to_board) < self.capacity:
                to_board.append(p)
            else:
                break

        if to_board:
            self.trip_count += 1

        for p in to_board:
            self.passengers_onboard.append(p)
            p.board_time = self.env.now
            p.wait_time = p.board_time - p.wait_time_start

            if p.arrival_floor == 0:
                if p in self.building_controller.passengers_in_lobby:
                    self.building_controller.passengers_in_lobby.remove(p)
            else:
                d_call = (
                    "up"
                    if p.destination_floor > p.arrival_floor
                    else "down"
                )
                if (
                    p
                    in self.building_controller.floor_call_queues[
                        p.arrival_floor
                    ][d_call]
                ):
                    self.building_controller.floor_call_queues[
                        p.arrival_floor
                    ][d_call].remove(p)

            boarded_ids.append(p.id)

        if boarded_ids:
            self.lift_movement_log.append({
                "time": self.env.now,
                "lift_id": self.id,
                "event": "board",
                "floor": curr_f,
                "passengers_ids": boarded_ids,
                "count": len(boarded_ids),
            })
            yield self.env.timeout(BOARD_TIME * len(boarded_ids))


def passenger_generator(env, building_controller, num_floors, guest_tracker):
    passenger_counter = itertools.count()
    while True:
        yield env.timeout(1)
        guest_id, arr_floor, dest_floor = (
            guest_tracker.get_available_guest_for_trip(env.now)
        )
        if guest_id is None:
            continue
        p_id = next(passenger_counter)
        passenger = Passenger(
            env, p_id, arr_floor, dest_floor, guest_id=guest_id
        )
        building_controller.add_passenger_call(passenger)


def run_single_simulation(run_id):
    env = simpy.Environment()
    guest_tracker = TripleTripGuestTracker(
        MAX_PASSENGERS_IN_BUILDING, GUESTS_PER_FLOOR
    )
    building_controller = BuildingController(env, NUM_FLOORS, guest_tracker)
    lifts = [
        Lift(env, i, building_controller, CAPACITY, NUM_FLOORS)
        for i in range(NUM_LIFTS)
    ]
    building_controller.lifts = lifts

    env.process(
        passenger_generator(env, building_controller, NUM_FLOORS, guest_tracker)
    )
    env.run(until=SIMULATION_DURATION)

    served = [
        p for p in building_controller.all_passengers if p.disembark_time != -1
    ]
    passenger_df = pd.DataFrame([
        {
            "Run ID": run_id,
            "Passenger ID": p.id,
            "Guest ID": p.guest_id,
            "Arrival Floor": p.arrival_floor,
            "Destination Floor": p.destination_floor,
            "Direction": p.direction,
            "Arrival Time": round(p.wait_time_start, 2),
            "Board Time": round(p.board_time, 2),
            "Disembark Time": round(p.disembark_time, 2),
            "Wait Time": round(p.wait_time, 2),
            "Trip Time": round(p.trip_time, 2),
            "Total Time": round(p.total_time, 2),
        }
        for p in served
    ])

    all_lift_logs = []
    for lift in lifts:
        all_lift_logs.extend(lift.lift_movement_log)
    all_lift_logs.sort(key=lambda x: x["time"])

    lift_log_data = []
    for entry in all_lift_logs:
        if entry["event"] == "board":
            lift_log_data.append({
                "Run ID": run_id,
                "Time": round(entry["time"], 2),
                "Lift ID": entry["lift_id"],
                "Event Type": "Board",
                "Floor": entry["floor"],
                "Passengers (IDs)": ", ".join(map(str, entry["passengers_ids"])),
                "Count": entry["count"],
                "From Floor": None,
                "To Floor": None,
                "Travel Duration": None,
            })
        elif entry["event"] == "disembark":
            lift_log_data.append({
                "Run ID": run_id,
                "Time": round(entry["time"], 2),
                "Lift ID": entry["lift_id"],
                "Event Type": "Disembark",
                "Floor": entry["floor"],
                "Passengers (IDs)": ", ".join(map(str, entry["passengers_ids"])),
                "Count": entry["count"],
                "From Floor": None,
                "To Floor": None,
                "Travel Duration": None,
            })
        elif entry["event"] == "moved_to_floor":
            lift_log_data.append({
                "Run ID": run_id,
                "Time": round(entry["time"], 2),
                "Lift ID": entry["lift_id"],
                "Event Type": "Travel",
                "Floor": None,
                "Passengers (IDs)": None,
                "Count": None,
                "From Floor": entry["from_floor"],
                "To Floor": entry["to_floor"],
                "Travel Duration": round(entry["travel_duration"], 2),
            })

    lift_log_df = pd.DataFrame(lift_log_data)

    round_trips = []
    for lift in lifts:
        lobby_arrivals = [
            e["time"]
            for e in lift.lift_movement_log
            if e["event"] == "moved_to_floor" and e["to_floor"] == 0
        ]
        for i in range(1, len(lobby_arrivals)):
            rtt_val = lobby_arrivals[i] - lobby_arrivals[i - 1]
            if 10.0 <= rtt_val <= 300.0:
                round_trips.append(rtt_val)

    avg_rtt = np.mean(round_trips) if round_trips else 0.0
    board_events = [
        e["count"]
        for e in all_lift_logs
        if e["event"] == "board" and e["floor"] == 0
    ]
    avg_passengers = np.mean(board_events) if board_events else 0.0

    run_summary = {
        "Run ID": run_id,
        "Simulation Duration (Hours)": SIMULATION_DURATION / 3600.0,
        "Total Passengers Generated": len(building_controller.all_passengers),
        "Passengers Served": len(served),
        "Avg Wait Time Overall": (
            passenger_df["Wait Time"].mean() if not passenger_df.empty else 0
        ),
        "Max Wait Time Overall": (
            passenger_df["Wait Time"].max() if not passenger_df.empty else 0
        ),
        "Median Wait Time Overall": (
            passenger_df["Wait Time"].median() if not passenger_df.empty else 0
        ),
        "90th Percentile Wait Time": (
            passenger_df["Wait Time"].quantile(0.9)
            if not passenger_df.empty
            else 0
        ),
        "Pct Under 30s Overall": (
            (passenger_df["Wait Time"] < 30).mean() * 100
            if not passenger_df.empty
            else 0
        ),
        "Avg Trip Time Overall": (
            passenger_df["Trip Time"].mean() if not passenger_df.empty else 0
        ),
        "Avg RTT": avg_rtt,
        "Avg Passengers Per Trip": avg_passengers,
        "HC5 Persons": (
            (300.0 / avg_rtt) * avg_passengers * len(lifts)
            if avg_rtt > 0
            else 0.0
        ),
    }
    return run_summary, passenger_df, lift_log_df


def generate_monte_carlo_reports(
    all_summaries,
    all_passengers_df,
    run1_passengers_df,
    run1_lift_log_df,
    filename="monte_carlo_elevator_2hr_3runs_simulation_report.xlsx",
):
    summary_df = pd.DataFrame(all_summaries)
    avg_metrics = {
        "Metric / KPI (Monte Carlo Averages)": [
            "Total Simulation Runs (Tests)",
            "Duration Per Test (Hours)",
            "Max Passenger Population",
            "Avg Total Passengers Generated per Test",
            "Avg Passengers Served per Test",
            "Mean Wait Time Overall (s)",
            "Mean Max Wait Time Overall (s)",
            "Mean Median Wait Time Overall (s)",
            "Mean 90th Percentile Wait Time (s)",
            "Mean % Waiting < 30s Overall",
            "Mean Trip Time Overall (s)",
            "--- CIBSE / ISO KPIs ---",
            "Mean Round Trip Time (RTT) (s)",
            "Mean Target Interval (INT) (s)",
            "Mean 5-Min Handling Capacity (HC5 - Persons)",
            "Mean 5-Min Handling Ratio (%HC)",
        ],
        "Monte Carlo Average Value": [
            len(all_summaries),
            f"{SIMULATION_DURATION / 3600.0:.1f} Hours",
            MAX_PASSENGERS_IN_BUILDING,
            f"{summary_df['Total Passengers Generated'].mean():.1f}",
            f"{summary_df['Passengers Served'].mean():.1f}",
            f"{summary_df['Avg Wait Time Overall'].mean():.2f}s",
            f"{summary_df['Max Wait Time Overall'].mean():.2f}s",
            f"{summary_df['Median Wait Time Overall'].mean():.2f}s",
            f"{summary_df['90th Percentile Wait Time'].mean():.2f}s",
            f"%{summary_df['Pct Under 30s Overall'].mean():.1f}",
            f"{summary_df['Avg Trip Time Overall'].mean():.2f}s",
            "",
            f"{summary_df['Avg RTT'].mean():.2f}s",
            f"{(summary_df['Avg RTT'].mean() / NUM_LIFTS):.2f}s",
            f"{summary_df['HC5 Persons'].mean():.1f} persons / 5 min",
            (
                f"%{(summary_df['HC5 Persons'].mean() / MAX_PASSENGERS_IN_BUILDING * 100):.1f}"
            ),
        ],
    }
    avg_metrics_df = pd.DataFrame(avg_metrics)

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        avg_metrics_df.to_excel(
            writer, sheet_name="Monte Carlo Summary", index=False
        )
        summary_df.to_excel(writer, sheet_name="Per Run Summary", index=False)
        run1_lift_log_df.to_excel(
            writer, sheet_name="Run 1 Lift Movements", index=False
        )
        run1_passengers_df.to_excel(
            writer, sheet_name="Run 1 Passenger Movements", index=False
        )
        all_passengers_df.to_excel(
            writer, sheet_name="All Passengers Raw Data", index=False
        )
    print(f"Rapor başarıyla kaydedildi: {filename}")


if __name__ == "__main__":
    all_summaries = []
    all_passengers_list = []
    run1_passengers_df, run1_lift_log_df = None, None

    for run in range(1, NUM_SIMULATION_RUNS + 1):
        print(
            f"Simülasyon Koşusu {run}/{NUM_SIMULATION_RUNS} çalıştırılıyor..."
        )
        summary, p_df, lift_log_df = run_single_simulation(run_id=run)
        if run == 1:
            run1_passengers_df, run1_lift_log_df = p_df, lift_log_df
        all_summaries.append(summary)
        all_passengers_list.append(p_df)

    output_filename = "monte_carlo_elevator_2hr_3runs_simulation_report.xlsx"
    generate_monte_carlo_reports(
        all_summaries,
        pd.concat(all_passengers_list, ignore_index=True),
        run1_passengers_df,
        run1_lift_log_df,
        filename=output_filename,
    )