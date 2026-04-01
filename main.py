from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import gspread
from gspread.utils import ValueInputOption
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

Scopes = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

ScriptDir = Path(__file__).resolve().parent
DefaultConfig = ScriptDir / "config.json"
StateFile = ScriptDir / ".synced_runs.json"

Gap = ""

DataStartRow = 4

TimelineIgtNames = [
    "enter_nether",
    "enter_bastion",
    "enter_fortress",
    "nether_travel",
    "nether_travel_blind",
    "enter_stronghold",
    "enter_end",
    "kill_ender_dragon",
]

TradeItems = [
    "enchanted_book",
    "iron_boots",
    "potion",
    "splash_potion",
    "iron_nugget",
    "ender_pearl",
    "quartz",
    "obsidian",
    "crying_obsidian",
    "fire_charge",
    "leather",
    "soul_sand",
    "nether_brick",
    "glowstone_dust",
    "gravel",
    "magma_cream",
]

MobNames = [
    "blaze",
    "chicken",
    "cod",
    "cow",
    "creeper",
    "enderman",
    "endermite",
    "ghast",
    "hoglin",
    "iron_golem",
    "pig",
    "piglin",
    "salmon",
    "sheep",
    "skeleton",
    "spider",
    "witch",
    "wither_skeleton",
    "zombie",
]

FoodNames = [
    "bread",
    "cooked_beef",
    "cooked_cod",
    "cooked_chicken",
    "cooked_mutton",
    "cooked_porkchop",
    "cooked_salmon",
    "enchanted_golden_apple",
    "golden_apple",
    "apple",
    "rotten_flesh",
    "golden_carrot",
    "carrot",
]

TravelStats = [
    ("travel_walk_on_water", "walk_on_water_one_cm"),
    ("travel_walk", "walk_one_cm"),
    ("travel_walk_under_water", "walk_under_water_one_cm"),
    ("travel_swim", "swim_one_cm"),
    ("travel_boat", "boat_one_cm"),
    ("travel_sprint", "sprint_one_cm"),
]


def LoadConfig(PathArg: Path) -> dict[str, Any]:
    if not PathArg.is_file():
        print(f"Missing config: {PathArg}\nCopy config.example.json to config.json and edit.", file=sys.stderr)
        sys.exit(1)
    with PathArg.open(encoding="utf-8") as F:
        Cfg = json.load(F)
    Base = PathArg.resolve().parent
    for Key in ("credentials_path", "records_dir"):
        if Key not in Cfg or not isinstance(Cfg[Key], str):
            continue
        P = Path(Cfg[Key])
        if not P.is_absolute():
            Cfg[Key] = str((Base / P).resolve())
    return Cfg


def LoadState() -> dict[str, Any]:
    if not StateFile.is_file():
        State: dict[str, Any] = {"files": {}, "logged_names": []}
        SaveState(State)
        return State
    with StateFile.open(encoding="utf-8") as F:
        State = json.load(F)
    State.setdefault("files", {})
    State.setdefault("logged_names", [])
    return State


def SaveState(State: dict[str, Any]) -> None:
    with StateFile.open("w", encoding="utf-8") as F:
        json.dump(State, F, indent=2)


def TimelineMap(Data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    Out: dict[str, dict[str, Any]] = {}
    for Entry in Data.get("timelines") or []:
        Name = Entry.get("name")
        if isinstance(Name, str):
            Out[Name] = Entry
    return Out


def InferSpawnBiome(Data: dict[str, Any]) -> str:
    Adv = (Data.get("advancements") or {}).get("minecraft:adventure/adventuring_time")
    if not isinstance(Adv, dict):
        return ""
    Crit = Adv.get("criteria")
    if not isinstance(Crit, dict):
        return ""
    Candidates: list[tuple[int, str]] = []
    for BiomeKey, Entry in Crit.items():
        if not isinstance(Entry, dict):
            continue
        if Entry.get("igt") != 0:
            continue
        Name = BiomeKey.split(":")[-1].replace("_", " ").title()
        Rta0 = Entry.get("rta") == 0
        Candidates.append((0 if Rta0 else 1, Name))
    if not Candidates:
        return ""
    Candidates.sort()
    return Candidates[0][1]


def FormatDateDdmmyyyy(Ms: Any) -> str:
    if Ms is None:
        return ""
    try:
        MsI = int(Ms)
    except (TypeError, ValueError):
        return ""
    try:
        Dt = datetime.fromtimestamp(MsI / 1000.0)
        return f"{Dt.day:02d}/{Dt.month:02d}/{Dt.year}"
    except (OSError, OverflowError, ValueError):
        return ""


def FormatIgtMs(Ms: Any) -> str:
    if Ms is None:
        return ""
    try:
        MsI = int(Ms)
    except (TypeError, ValueError):
        return ""
    if MsI < 0:
        return ""
    TotalSec = MsI // 1000
    H = TotalSec // 3600
    M = (TotalSec % 3600) // 60
    S = TotalSec % 60
    return f"{H}:{M:02d}:{S:02d}"


def FirstPlayerStats(Data: dict[str, Any]) -> dict[str, Any]:
    StatsRoot = Data.get("stats")
    if not isinstance(StatsRoot, dict):
        return {}
    for _, Block in StatsRoot.items():
        if isinstance(Block, dict):
            Inner = Block.get("stats")
            if isinstance(Inner, dict):
                return Inner
    return {}


def Cat(Inner: dict[str, Any], Name: str) -> dict[str, Any]:
    D = Inner.get(Name)
    return D if isinstance(D, dict) else {}


def StatNum(Inner: dict[str, Any], Category: str, ItemKey: str) -> int:
    V = Cat(Inner, Category).get(ItemKey)
    if V is None:
        return 0
    try:
        return int(V)
    except (TypeError, ValueError):
        return 0


def CmToBlocks(Cm: int) -> int:
    if Cm <= 0:
        return 0
    return Cm // 100


def BuildHeaderGroups() -> list[list[str]]:
    G1 = ["date"]
    G2 = ["rta", *TimelineIgtNames, "igt"]
    G3 = ["gold_dropped", "blaze_rods", "blazes_killed", "flint_picked_up", "gravel_mined"]
    G4 = ["deaths", "jumps", "eyes_used", "ender_pearls_used", "obsidian_placed"]
    G5 = ["stone_mined", "netherrack_mined"]
    G6 = [f"trade_{T}" for T in TradeItems]
    G7 = [f"killed_{M}" for M in MobNames]
    G8 = [f"eaten_{F}" for F in FoodNames]
    G9 = [H for H, _ in TravelStats]
    return [G1, G2, G3, G4, G5, G6, G7, G8, G9]


def AllHeaders() -> list[str]:
    Groups = BuildHeaderGroups()
    Out: list[str] = []
    for I, G in enumerate(Groups):
        if I > 0:
            Out.append(Gap)
        Out.extend(G)
    return Out


def ColLetterOneBased(N: int) -> str:
    if N < 1:
        raise ValueError("column index must be >= 1")
    S = ""
    while N:
        N, R = divmod(N - 1, 26)
        S = chr(65 + R) + S
    return S


def BuildRow(Data: dict[str, Any], SourceFile: str) -> list[Any] | None:
    if not Data.get("is_completed"):
        return None
    if Data.get("is_cheat_allowed", False):
        return None

    Inner = FirstPlayerStats(Data)
    Timelines = TimelineMap(Data)

    G1 = [FormatDateDdmmyyyy(Data.get("date"))]

    FinalRtaMs = Data.get("final_rta", Data.get("rta"))
    G2: list[Any] = [
        FormatIgtMs(FinalRtaMs),
    ]
    for Name in TimelineIgtNames:
        T = Timelines.get(Name)
        G2.append(FormatIgtMs(T.get("igt")) if T else "")
    G2.append(FormatIgtMs(Data.get("final_igt")))

    G3 = [
        StatNum(Inner, "minecraft:dropped", "minecraft:gold_ingot"),
        StatNum(Inner, "minecraft:picked_up", "minecraft:blaze_rod"),
        StatNum(Inner, "minecraft:killed", "minecraft:blaze"),
        StatNum(Inner, "minecraft:picked_up", "minecraft:flint"),
        StatNum(Inner, "minecraft:mined", "minecraft:gravel"),
    ]

    G4 = [
        StatNum(Inner, "minecraft:custom", "minecraft:deaths"),
        StatNum(Inner, "minecraft:custom", "minecraft:jump"),
        StatNum(Inner, "minecraft:used", "minecraft:ender_eye"),
        StatNum(Inner, "minecraft:used", "minecraft:ender_pearl"),
        StatNum(Inner, "minecraft:used", "minecraft:obsidian"),
    ]

    G5 = [
        StatNum(Inner, "minecraft:mined", "minecraft:stone"),
        StatNum(Inner, "minecraft:mined", "minecraft:netherrack"),
    ]

    G6 = [StatNum(Inner, "minecraft:picked_up", f"minecraft:{T}") for T in TradeItems]

    G7 = [StatNum(Inner, "minecraft:killed", f"minecraft:{M}") for M in MobNames]

    G8 = [StatNum(Inner, "minecraft:used", f"minecraft:{F}") for F in FoodNames]

    G9 = []
    for _, Key in TravelStats:
        G9.append(CmToBlocks(StatNum(Inner, "minecraft:custom", f"minecraft:{Key}")))

    Groups = [G1, G2, G3, G4, G5, G6, G7, G8, G9]
    Out: list[Any] = []
    for I, G in enumerate(Groups):
        if I > 0:
            Out.append(Gap)
        Out.extend(G)
    return Out


def GetWorksheet(Cfg: dict[str, Any]):
    CredPath = Path(Cfg["credentials_path"])
    if not CredPath.is_file():
        print(f"Credentials file not found: {CredPath}", file=sys.stderr)
        sys.exit(1)

    Gc = gspread.service_account(filename=str(CredPath), scopes=Scopes)
    Sh = Gc.open_by_key(Cfg["spreadsheet_id"])
    Name = Cfg.get("worksheet_name") or "Raw Data"
    try:
        return Sh.worksheet(Name)
    except gspread.WorksheetNotFound:
        return Sh.add_worksheet(title=Name, rows=1000, cols=120)


def NormalizeCell(C: Any) -> str:
    if C is None:
        return ""
    return str(C)


def EnsureHeaders(Ws, Headers: list[str]) -> None:
    Existing = Ws.row_values(1)
    if not Existing or not any(NormalizeCell(Cell).strip() for Cell in Existing):
        Last = ColLetterOneBased(len(Headers))
        Ws.update(
            [Headers],
            range_name=f"A1:{Last}1",
            value_input_option=ValueInputOption.user_entered,
        )
        return
    NormExisting = [NormalizeCell(C) for C in Existing[: len(Headers)]]
    NormHeaders = list(Headers)
    if NormExisting != NormHeaders:
        print(
            "Warning: row 1 headers do not match expected columns. "
            "Append anyway; fix headers manually or use a fresh sheet tab.",
            file=sys.stderr,
        )


def AppendRow(Ws, Row: list[Any], Headers: list[str]) -> None:
    EnsureHeaders(Ws, Headers)
    N = len(Row)
    if N == 0:
        return
    Ws.insert_row(
        Row,
        index=DataStartRow,
        value_input_option=ValueInputOption.user_entered,
        inherit_from_before=False,
    )


def ProcessFile(
    PathArg: Path,
    Cfg: dict[str, Any],
    Ws,
    Headers: list[str],
    State: dict[str, Any],
    Force: bool,
) -> str:
    Key = str(PathArg.resolve())
    try:
        St = PathArg.stat()
    except OSError:
        return "stat_error"

    Sig = f"{St.st_mtime_ns}:{St.st_size}"
    FilesState = State.setdefault("files", {})
    LoggedNames: list[str] = State.setdefault("logged_names", [])
    if not Force and PathArg.name in LoggedNames:
        return "synced"
    if not Force and FilesState.get(Key) == Sig:
        return "synced"

    try:
        with PathArg.open(encoding="utf-8") as F:
            Data = json.load(F)
    except (OSError, json.JSONDecodeError) as E:
        print(f"Skip (read error): {PathArg}: {E}", file=sys.stderr)
        return "read_error"

    Row = BuildRow(Data, PathArg.name)
    if Row is None:
        FilesState[Key] = Sig
        if Data.get("is_completed") and Data.get("is_cheat_allowed", False):
            print(
                "Skipped (not logged): is_cheat_allowed is true.",
                file=sys.stderr,
            )
            return "skipped_cheat"
        return "incomplete"

    AppendRow(Ws, Row, Headers)
    FilesState[Key] = Sig
    if PathArg.name not in LoggedNames:
        LoggedNames.append(PathArg.name)
    Biome = InferSpawnBiome(Data)
    if Biome:
        print(f"Uploaded: {PathArg.name}  |  spawn biome: {Biome}")
    else:
        print(f"Uploaded: {PathArg.name}  |  spawn biome: (unknown)")
    return "uploaded"


def ScanDirectory(Cfg: dict[str, Any], State: dict[str, Any], Force: bool) -> None:
    Root = Path(Cfg["records_dir"])
    if not Root.is_dir():
        print(f"records_dir is not a directory: {Root}", file=sys.stderr)
        sys.exit(1)

    Ws = GetWorksheet(Cfg)
    Headers = AllHeaders()

    Paths = sorted(Root.glob("*.json"), key=lambda PathItem: PathItem.stat().st_mtime)
    if not Paths:
        print(f"No *.json files in {Root}", file=sys.stderr)
        return

    print(f"Scanning {len(Paths)} JSON file(s) in {Root}")
    Counts: dict[str, int] = {}
    for PathItem in Paths:
        Outcome = ProcessFile(PathItem, Cfg, Ws, Headers, State, Force=Force)
        Counts[Outcome] = Counts.get(Outcome, 0) + 1
    SaveState(State)

    U = Counts.get("uploaded", 0)
    S = Counts.get("synced", 0)
    Inc = Counts.get("incomplete", 0)
    Cheat = Counts.get("skipped_cheat", 0)
    Err = Counts.get("read_error", 0) + Counts.get("stat_error", 0)
    Parts = [f"{U} uploaded", f"{S} skipped (already synced)"]
    if Inc:
        Parts.append(f"{Inc} skipped (run not completed)")
    if Cheat:
        Parts.append(f"{Cheat} skipped (cheats allowed)")
    if Err:
        Parts.append(f"{Err} errors")
    print("Done: " + ", ".join(Parts) + ".")
    if S and not Force:
        print("Tip: re-upload all files with --scan-all --force (ignores .synced_runs.json).")


class JsonHandler(FileSystemEventHandler):
    def __init__(self, Cfg: dict[str, Any], State: dict[str, Any], Records: Path):
        super().__init__()
        self.Cfg = Cfg
        self.State = State
        self.Records = Records.resolve()
        self.WsCached = None
        self.HeadersCached: list[str] | None = None

    @property
    def Ws(self):
        if self.WsCached is None:
            self.WsCached = GetWorksheet(self.Cfg)
        return self.WsCached

    @property
    def Headers(self) -> list[str]:
        if self.HeadersCached is None:
            self.HeadersCached = AllHeaders()
        return self.HeadersCached

    def on_created(self, Event):
        self.Handle(Event)

    def on_modified(self, Event):
        self.Handle(Event)

    def Handle(self, Event) -> None:
        if getattr(Event, "is_directory", False):
            return
        PathArg = Path(Event.src_path)
        if PathArg.suffix.lower() != ".json":
            return
        if PathArg.resolve().parent != self.Records:
            return
        time.sleep(0.6)
        ProcessFile(PathArg, self.Cfg, self.Ws, self.Headers, self.State, Force=False)
        SaveState(self.State)


def ResolveJsonPath(P: Path, Cfg: dict[str, Any]) -> Path:
    P = P.expanduser()
    if P.is_file():
        return P.resolve()
    if not P.is_absolute():
        InRecords = Path(Cfg["records_dir"]) / P.name
        if InRecords.is_file():
            return InRecords.resolve()
    return P.resolve()


def Main() -> None:
    Parser = argparse.ArgumentParser(description="SpeedrunIGT JSON -> Google Sheets")
    Parser.add_argument("--config", type=Path, default=DefaultConfig, dest="Config", help="Path to config.json")
    Parser.add_argument(
        "--file",
        type=Path,
        dest="File",
        help="Process one JSON file (path or filename under records_dir)",
    )
    Parser.add_argument("--watch", action="store_true", dest="Watch", help="Watch records_dir for new/changed JSON")
    Parser.add_argument("--scan-all", action="store_true", dest="ScanAll", help="Upload all *.json in records_dir once")
    Parser.add_argument(
        "--force",
        action="store_true",
        dest="Force",
        help="Re-upload even if file is unchanged (see .synced_runs.json state)",
    )
    Args = Parser.parse_args()

    Cfg = LoadConfig(Args.Config)
    State = LoadState()
    Headers = AllHeaders()

    if Args.File:
        PathArg = ResolveJsonPath(Args.File, Cfg)
        if not PathArg.is_file():
            print(
                f"Not a file: {Args.File}\n"
                f"  Tried: {PathArg.resolve()}\n"
                f"  Also pass a full path or a .json name that exists in records_dir.",
                file=sys.stderr,
            )
            sys.exit(1)
        Ws = GetWorksheet(Cfg)
        Outcome = ProcessFile(PathArg, Cfg, Ws, Headers, State, Force=Args.Force)
        SaveState(State)
        if Outcome == "synced":
            print(
                "Skipped: this file is already synced (unchanged). "
                "Use --file ... --force to upload again.",
                file=sys.stderr,
            )
        elif Outcome == "incomplete":
            print("Skipped: run is not completed (is_completed is false).", file=sys.stderr)
        elif Outcome == "stat_error":
            print(f"Error: could not read file stats: {PathArg}", file=sys.stderr)
            sys.exit(1)
        return

    if Args.ScanAll:
        ScanDirectory(Cfg, State, Force=Args.Force)
        return

    if Args.Watch:
        Records = Path(Cfg["records_dir"])
        if not Records.is_dir():
            print(f"records_dir is not a directory: {Records}", file=sys.stderr)
            sys.exit(1)
        Handler = JsonHandler(Cfg, State, Records)
        ObserverInstance = Observer()
        ObserverInstance.schedule(Handler, str(Records), recursive=False)
        ObserverInstance.start()
        print(f"Watching {Records} — Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ObserverInstance.stop()
        ObserverInstance.join()
        SaveState(Handler.State)
        return

    Parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    Main()
