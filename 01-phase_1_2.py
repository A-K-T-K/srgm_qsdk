import os
import re
import math
import time
import datetime
import subprocess
import requests
import zipfile
import json
import pandas as pd
from typing import Dict, List, Optional, Tuple, Set
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIGURATION & PROTOCOL SWITCHES
# ============================================================

if os.environ.get("GIT_EXECUTABLE_PATH"):
    os.environ["GIT_PYTHON_GIT_EXECUTABLE"] = os.environ["GIT_EXECUTABLE_PATH"]

GITHUB_TOKEN = ""

if not GITHUB_TOKEN:
    print("\n[Warning] No GITHUB_MINING_TOKEN set. API calls will be unauthenticated (60 req/hour limit).")

SENSITIVITY_MODE = False  
DEFAULT_CUES = ["bug", "fix", "error", "defect", "regression"]
SENSITIVITY_CUES = ["fault", "incorrect", "failure"]
KEYWORDS = DEFAULT_CUES + (SENSITIVITY_CUES if SENSITIVITY_MODE else [])

DUAL_ANNOTATOR = False  
MS_ECOSYSTEM_MODE = "both" 

REPOS = {
    "qiskit": "https://github.com/Qiskit/qiskit.git",
    "cirq": "https://github.com/quantumlib/cirq.git",
    "pennylane": "https://github.com/PennyLaneAI/pennylane.git"
}
if MS_ECOSYSTEM_MODE in ("historical", "both"):
    REPOS["qsharp"] = "https://github.com/microsoft/qsharp.git"
if MS_ECOSYSTEM_MODE in ("modern", "both"):
    REPOS["qdk"] = "https://github.com/microsoft/qdk.git"

REPO_FULL_NAMES = {k: v.replace("https://github.com/", "").replace(".git", "") for k, v in REPOS.items()}
CUTOFF_DATE = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

BUG_PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.IGNORECASE)
ISSUE_REF_PATTERN = re.compile(r"#(\d+)")
CLOSES_PATTERN = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
} if GITHUB_TOKEN else {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

OUTPUT_DIR = "output"
DATA_DIR = "data"
REPO_DIR = os.path.join(DATA_DIR, "repos")

PHASE1_CHECKPOINT = os.path.join(OUTPUT_DIR, "phase1_candidate_checkpoint.csv")
PHASE1_FINAL = os.path.join(OUTPUT_DIR, "phase1_candidate_pool.csv")
PHASE1_TAGS = os.path.join(OUTPUT_DIR, "phase1_tags.csv")
PHASE2_SAMPLE = os.path.join(OUTPUT_DIR, "phase2_manual_validation_sample.csv")
PHASE2_AUDIT = os.path.join(OUTPUT_DIR, "phase2_second_pass_audit_sample.csv")
PHASE2_GUIDE = os.path.join(OUTPUT_DIR, "phase2_annotation_guide.csv")
PHASE2_STATS = os.path.join(OUTPUT_DIR, "phase2_sampling_stats.csv")
PHASE2_CASES = os.path.join(OUTPUT_DIR, "phase2_representative_cases.csv")
PHASE3_THRESHOLDS = os.path.join(OUTPUT_DIR, "phase3_inclusion_thresholds.csv")
SKIPPED_COMMITS_LOG = os.path.join(OUTPUT_DIR, "phase1_skipped_commits.csv")
REPRODUCIBILITY_MANIFEST = os.path.join(OUTPUT_DIR, "reproducibility_manifest.csv")
ZIP_ARCHIVE_OUTPUT = os.path.join(OUTPUT_DIR, "reproducibility_package.zip")

SAVE_EVERY = 250
RANDOM_SEED = 42
CONFIDENCE_Z = 1.96
MARGIN_OF_ERROR = 0.05
P_EST = 0.5
SECOND_PASS_AUDIT_FRACTION = 0.2
REQUEST_SLEEP = 0.15  # Optimized sleep for faster bulk paging
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
MAX_WORKERS = 4  # Safe concurrent worker pool to avoid abuse rate limits

SKIP_COMMITS = {"715f8e3e914c08717c4ac5e07012d3254bcd1892"}

COLLABORATOR_CACHE: Dict[str, bool] = {}
ISSUE_CACHE: Dict[str, dict] = {}
CACHE_FILE = os.path.join(DATA_DIR, "api_cache.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPO_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# PERSISTENT CACHE CORE ENGINES
# ============================================================

def load_api_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                COLLABORATOR_CACHE.update(data.get("collaborator", {}))
                ISSUE_CACHE.update(data.get("issue", {}))
            print(f"[Cache Storage] Successfully mounted {len(COLLABORATOR_CACHE)} collaborators and {len(ISSUE_CACHE)} issues from disk.")
        except Exception as e:
            print(f"[Cache Error] Warning: Could not parse persistent cache storage: {e}. Starting fresh.")

def save_api_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "collaborator": COLLABORATOR_CACHE,
                "issue": ISSUE_CACHE
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Cache Error] Warning: Failed to write persistent cache payload to file system: {e}")

load_api_cache()

# ============================================================
# CORE UTILITIES & GIT RUNNERS
# ============================================================

def run_git(repo_path: str, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", repo_path] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        print(f"\n[Git Fatal Error] Command failed: {' '.join(cmd)}")
        print(f"[Git Stderr Output] {e.stderr.strip()}")
        raise e

def ensure_full_clone(short_name: str, repo_url: str) -> str:
    local_path = os.path.join(REPO_DIR, short_name)
    git_dir = os.path.join(local_path, ".git")
    
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True)

    if os.path.exists(local_path) and not os.path.isdir(git_dir):
        print(f"Notice: Found corrupted or incomplete workspace at {local_path}. Wiping for a clean re-clone...")
        shutil.rmtree(local_path, ignore_errors=True)

    if not os.path.exists(local_path):
        print(f"Cloning {repo_url} -> {local_path} (This may take a few minutes)...")
        subprocess.run(["git", "clone", repo_url, local_path], check=True, capture_output=True, text=True)
    else:
        print(f"Updating existing clone: {local_path}")
        run_git(local_path, ["checkout", "-"], check=False)
        subprocess.run(["git", "-C", local_path, "fetch", "--all", "--tags", "--prune"], check=True, capture_output=True, text=True)
        
    return local_path

# ============================================================
# METADATA ENRICHMENT & COLLABORATOR ENGINES
# ============================================================

def safe_request(url: str, params: Optional[Dict] = None) -> Optional[requests.Response]:
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            
            if response.status_code == 403 and "X-RateLimit-Remaining" in response.headers:
                if response.headers.get("X-RateLimit-Remaining") == "0":
                    reset_time = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                    sleep_duration = max(reset_time - int(time.time()), 5)
                    print(f"\n[API Warning] Rate limit hit. Cooling down for {sleep_duration} seconds...")
                    time.sleep(sleep_duration)
                    continue
            
            return response
            
        except requests.exceptions.RequestException as e:
            backoff = REQUEST_SLEEP * (attempt + 2) * 2
            print(f"\n[Network Warning] Attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. Retrying in {backoff}s...")
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
            else:
                print(f"[Fatal] Max retries reached for URL: {url}")
                return None
    return None

def is_external_contributor(repo_full_name: str, username: str) -> bool:
    if not username:
        return False
        
    lower_user = username.lower()
    if lower_user in ["github-actions", "dependabot", "dependabot[bot]"]:
        return False
    
    cache_key = f"{repo_full_name}/{lower_user}"
    if cache_key in COLLABORATOR_CACHE:
        return COLLABORATOR_CACHE[cache_key]
    
    url = f"https://api.github.com/repos/{repo_full_name}/collaborators/{username}"
    res = safe_request(url)
    
    if res is not None:
        if res.status_code == 204:
            COLLABORATOR_CACHE[cache_key] = False  
            return False
        if res.status_code == 404:
            COLLABORATOR_CACHE[cache_key] = True   
            return True
            
    COLLABORATOR_CACHE[cache_key] = True
    return True 

def extract_issue_numbers(text: str) -> List[str]:
    refs = set(ISSUE_REF_PATTERN.findall(text or ""))
    refs.update(CLOSES_PATTERN.findall(text or ""))
    return sorted(refs, key=lambda x: int(x) if str(x).isdigit() else 999999)

def get_issue_data(repo_full_name: str, issue_number: str) -> Optional[dict]:
    cache_key = f"{repo_full_name}#{issue_number}"
    if cache_key in ISSUE_CACHE:
        return ISSUE_CACHE[cache_key]

    url = f"https://api.github.com/repos/{repo_full_name}/issues/{issue_number}"
    res = safe_request(url)
    if not res or res.status_code != 200:
        return None
    
    data = res.json()
    author = data.get("user", {}).get("login", "")
    
    issue_meta = {
        "issue_number": issue_number,
        "issue_title": data.get("title"),
        "issue_labels": [l.get("name", "") for l in data.get("labels", [])],
        "is_pr": "pull_request" in data,
        "author_login": author,
        "is_external": is_external_contributor(repo_full_name, author)
    }
    
    ISSUE_CACHE[cache_key] = issue_meta
    return issue_meta

def prefetch_labeled_issues_for_repo(repo_full_name: str):
    """Fills the cache rapidly by paginating labeled bug streams instead of single issue hits."""
    print(f"Executing asynchronous query pipeline prefetch for: {repo_full_name}")
    
    def fetch_page(label: str, page: int):
        url = f"https://api.github.com/repos/{repo_full_name}/issues"
        params = {"labels": label, "state": "all", "per_page": 100, "page": page}
        return safe_request(url, params=params), label, page

    labels = ["bug", "defect", "regression"]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for label in labels:
            for page in range(1, 16):  # Prefetch top 15 pages (1500 items) per label stream quickly
                futures.append(executor.submit(fetch_page, label, page))
        
        for fut in as_completed(futures):
            res, label, page = fut.result()
            if res and res.status_code == 200:
                data = res.json()
                if not data:
                    continue
                for issue in data:
                    num = str(issue.get("number"))
                    author = issue.get("user", {}).get("login", "")
                    cache_key = f"{repo_full_name}#{num}"
                    if cache_key not in ISSUE_CACHE:
                        ISSUE_CACHE[cache_key] = {
                            "issue_number": num,
                            "issue_title": issue.get("title"),
                            "issue_labels": [lab.get("name", "") for lab in issue.get("labels", [])],
                            "is_pr": "pull_request" in issue,
                            "author_login": author,
                            "is_external": False # Will be lazily resolved fallback if needed, defaults false to save speed
                        }

# ============================================================
# LOCAL REPOSITORY ARCHITECTURE
# ============================================================

def get_verified_default_branch(repo_path: str) -> str:
    result = run_git(repo_path, ["symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
    if result.returncode == 0 and (result.stdout or "").strip():
        return result.stdout.strip().split("/")[-1]
    
    for fallback in ["main", "master", "development"]:
        branch_check = run_git(repo_path, ["show-ref", f"refs/heads/{fallback}"], check=False)
        if branch_check.returncode == 0:
            return fallback
    return "main"

def get_tag_table(repo_path: str, repo_full_name: str, repository: str) -> pd.DataFrame:
    fmt = "%(refname:short)|%(objectname)|%(taggerdate:iso8601)|%(creatordate:iso8601)"
    result = run_git(repo_path, ["for-each-ref", "--sort=creatordate", f"--format={fmt}", "refs/tags"], check=True)
    rows = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        tag, sha, tagger_dt, creator_dt = parts
        chosen_dt = tagger_dt if tagger_dt.strip() else creator_dt
        rows.append({
            "repository": repository,
            "repo_full_name": repo_full_name,
            "tag": tag,
            "tag_commit_sha": sha,
            "tag_commit_date": pd.to_datetime(chosen_dt, utc=True, errors="coerce")
        })
    df = pd.DataFrame(rows)
    return df.sort_values(["tag_commit_date", "tag"]).reset_index(drop=True) if not df.empty else df

# ============================================================
# EXHAUSTIVE ENGINE TRACKING TIMELINES
# ============================================================

def determine_exhaustive_v0(repo_path: str, repo_full_name: str, tags_df: pd.DataFrame) -> Tuple[Optional[str], pd.Timestamp]:
    if tags_df.empty or len(tags_df) < 2:
        return None, pd.NaT

    for idx, row in tags_df.iterrows():
        tag = row["tag"]
        tag_date = row["tag_commit_date"]
        
        prev_tag = tags_df.iloc[idx - 1]["tag"] if idx > 0 else None
        rev_range = f"{prev_tag}..{tag}" if prev_tag else tag
        res = run_git(repo_path, ["rev-list", "--no-merges", rev_range], check=False)
        
        has_external_activity = False
        if res.returncode == 0:
            commit_shas = [sha.strip() for sha in (res.stdout or "").splitlines() if sha.strip()]
            for sha in commit_shas[:15]:  # Performance optimization: check top 15 boundary points only
                res_msg = run_git(repo_path, ["log", "--format=%B", "-n", "1", sha], check=False)
                if res_msg.returncode == 0:
                    issues = extract_issue_numbers(res_msg.stdout)
                    for num in issues:
                        issue_data = get_issue_data(repo_full_name, num)
                        if issue_data and issue_data.get("is_external"):
                            has_external_activity = True
                            break
                if has_external_activity:
                    break
                    
        if has_external_activity and (idx < len(tags_df) - 1):
            return tag, tag_date

    return tags_df.iloc[0]["tag"], tags_df.iloc[0]["tag_commit_date"]

# ============================================================
# CHECKPOINT HANDLERS
# ============================================================

def load_checkpoint() -> Tuple[pd.DataFrame, Set[str]]:
    if os.path.exists(PHASE1_CHECKPOINT):
        try:
            df = pd.read_csv(PHASE1_CHECKPOINT)
            return df, set(df["commit_hash"].astype(str)) if "commit_hash" in df.columns else set()
        except:
            pass
    return pd.DataFrame(), set()

def save_checkpoint(existing_df: pd.DataFrame, buffer: List[dict]) -> Tuple[pd.DataFrame, Set[str]]:
    combined = pd.concat([existing_df, pd.DataFrame(buffer)], ignore_index=True).drop_duplicates(subset=["repository", "commit_hash"])
    combined.to_csv(PHASE1_CHECKPOINT, index=False)
    save_api_cache()
    return combined, set(combined["commit_hash"].astype(str))

# ============================================================
# PHASE 1: BROAD PRE-FILTER EXTRACTION PIPELINE
# ============================================================

def phase1_build_candidate_pool():
    existing_df, processed_hashes = load_checkpoint()
    buffer = []
    all_tags_export = []
    manifest_rows = []

    for short_name, repo_url in REPOS.items():
        repo_full_name = REPO_FULL_NAMES[short_name]
        print(f"\n[Phase 1] Executing Optimized Data Crawl For {repo_full_name} ...")

        repo_path = ensure_full_clone(short_name, repo_url)
        
        head_sha_res = run_git(repo_path, ["rev-parse", "HEAD"], check=True)
        frozen_sha = head_sha_res.stdout.strip()
        run_git(repo_path, ["checkout", "--detach", frozen_sha], check=True)
        
        manifest_rows.append({
            "repository": short_name,
            "frozen_mining_head_sha": frozen_sha,
            "execution_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })

        default_branch = get_verified_default_branch(repo_path)
        raw_tags = get_tag_table(repo_path, repo_full_name, short_name)
        
        # Prefetch to warm up the global issue cache before running main logic loops
        prefetch_labeled_issues_for_repo(repo_full_name)

        v0_tag, v0_date = determine_exhaustive_v0(repo_path, repo_full_name, raw_tags)
        print(f"Exhaustive V0 Boundary Identified: {v0_tag} ({v0_date})")

        tags_df = raw_tags.copy()
        if not tags_df.empty:
            tags_df = tags_df[(tags_df["tag_commit_date"] >= v0_date) & (tags_df["tag_commit_date"] <= CUTOFF_DATE)].reset_index(drop=True)

        if tags_df.empty:
            print(f"Zero matching versions inside the window for {repo_full_name}")
            continue
            
        all_tags_export.append(tags_df.copy())

        print("Traversing local commit timeline...")
        log_res = run_git(repo_path, ["log", f"origin/{default_branch}", "--no-merges", "--format=%H|%cI|%B%x00"])
        
        ancestor_cache = {}
        
        if log_res.returncode == 0:
            commit_blocks = log_res.stdout.split("\x00")
            
            for block in commit_blocks:
                block = block.strip()
                if not block or "|" not in block:
                    continue
                
                parts = block.split("|", 2)
                if len(parts) < 3:
                    continue
                c_sha, c_date, c_msg = parts
                
                # TIMELINE OPTIMIZATION: Break early if we run back into a previously mined domain sequence
                if c_sha in processed_hashes:
                    print(f"Reached verified checkpoint history boundary at {c_sha}. Progress secured.")
                    break
                
                if c_sha in SKIP_COMMITS:
                    continue
                    
                try:
                    c_date_utc = pd.to_datetime(c_date, utc=True)
                except Exception:
                    continue
                    
                if c_date_utc > CUTOFF_DATE or c_date_utc < v0_date:
                    continue

                message_hit = bool(BUG_PATTERN.search(c_msg))
                issue_refs = extract_issue_numbers(c_msg)
                
                label_hit = False
                explicit_bug_label = False
                
                for ref in issue_refs:
                    cache_key = f"{repo_full_name}#{ref}"
                    if cache_key in ISSUE_CACHE:
                        meta = ISSUE_CACHE[cache_key]
                        lower_labs = [l.lower() for l in meta.get("issue_labels", [])]
                        if any("bug" in l for l in lower_labs):
                            explicit_bug_label = True
                        if any("bug" in l or "defect" in l or "regression" in l for l in lower_labs):
                            label_hit = True
                    else:
                        # Fallback for cache misses safely without network spamming unless keyword matched
                        if message_hit:
                            issue_meta = get_issue_data(repo_full_name, ref)
                            if issue_meta:
                                lower_labs = [l.lower() for l in issue_meta["issue_labels"]]
                                if any("bug" in l for l in lower_labs):
                                    explicit_bug_label = True
                                if any("bug" in l or "defect" in l or "regression" in l for l in lower_labs):
                                    label_hit = True

                if not (message_hit or label_hit or explicit_bug_label):
                    continue

                mapped_releases = []
                for _, tag_row in tags_df.iterrows():
                    t_sha = tag_row["tag_commit_sha"]
                    key = (c_sha, t_sha)
                    if key not in ancestor_cache:
                        ancestor_cache[key] = (run_git(repo_path, ["merge-base", "--is-ancestor", c_sha, t_sha], check=False).returncode == 0)
                    
                    if ancestor_cache[key]:
                        mapped_releases.append(tag_row)

                if not mapped_releases:
                    continue

                mapped_releases = sorted(mapped_releases, key=lambda x: x["tag_commit_date"])
                primary_release = mapped_releases[0]
                is_backported = 1 if len(mapped_releases) > 1 else 0

                buffer.append({
                    "repository": short_name,
                    "commit_hash": c_sha,
                    "commit_date": c_date_utc.isoformat(),
                    "message": c_msg,
                    "mapped_release_tag": primary_release["tag"],
                    "mapped_release_date": primary_release["tag_commit_date"].isoformat(),
                    "has_bug_keyword_in_message": 1 if message_hit else 0,
                    "has_bug_label": 1 if label_hit else 0,
                    "has_explicit_bug_label": 1 if explicit_bug_label else 0,
                    "is_backported": is_backported,
                    "v0_baseline_tag": v0_tag,
                    "sensitivity_mode_active": 1 if SENSITIVITY_MODE else 0
                })

                if len(buffer) >= SAVE_EVERY:
                    existing_df, processed_hashes = save_checkpoint(existing_df, buffer)
                    buffer = []

    if buffer:
        existing_df, processed_hashes = save_checkpoint(existing_df, buffer)

    # Re-read final state to merge cleanly
    existing_df, _ = load_checkpoint()
    if not existing_df.empty:
        existing_df = existing_df.drop_duplicates(subset=["repository", "commit_hash"]).sort_values(["repository", "commit_date"]).reset_index(drop=True)
        existing_df.to_csv(PHASE1_FINAL, index=False)

    if all_tags_export:
        pd.concat(all_tags_export, ignore_index=True).to_csv(PHASE1_TAGS, index=False)
    if manifest_rows:
        pd.DataFrame(manifest_rows).to_csv(REPRODUCIBILITY_MANIFEST, index=False)

    save_api_cache()

# ============================================================
# PHASE 2: MANUAL VALIDATION EXTRACTION & TESTING
# ============================================================

def finite_population_sample_size(N: int, z: float = CONFIDENCE_Z, e: float = MARGIN_OF_ERROR, p: float = P_EST) -> int:
    if N <= 0:
        return 0
    n0 = (z ** 2 * p * (1 - p)) / (e ** 2)
    return min(N, math.ceil(n0 / (1 + ((n0 - 1) / N))))

def phase2_generate_validation_samples():
    if not os.path.exists(PHASE1_FINAL):
        return
    df = pd.read_csv(PHASE1_FINAL)
    if df.empty:
        return

    samples, stats = [], []
    for repo_name, sub in df.groupby("repository", sort=True):
        sub = sub.copy().reset_index(drop=True)
        N = len(sub)
        n = finite_population_sample_size(N)
        
        sampled = sub.sample(n=n, replace=False, random_state=RANDOM_SEED).copy()
        sampled["population_N"] = N
        sampled["sample_n"] = n
        
        if DUAL_ANNOTATOR:
            sampled["annotator_1_label"] = ""
            sampled["annotator_2_label"] = ""
            sampled["adjudicated_label"] = ""
        else:
            sampled["annotator_label"] = ""
            sampled["cooling_period_completed"] = 0
            sampled["audit_timestamp"] = ""
            
        sampled["inclusion_rule_triggered"] = ""
        sampled["exclusion_rule_triggered"] = ""
        sampled["annotation_notes"] = ""
        
        samples.append(sampled)
        stats.append({"repository": repo_name, "population_N": N, "sample_n": n})

    sample_df = pd.concat(samples, ignore_index=True).sort_values(["repository", "commit_date"]).reset_index(drop=True)
    sample_df.to_csv(PHASE2_SAMPLE, index=False)
    pd.DataFrame(stats).to_csv(PHASE2_STATS, index=False)

    audit_frames = []
    for repo_name, sub in sample_df.groupby("repository", sort=True):
        audit_n = max(1, math.ceil(len(sub) * SECOND_PASS_AUDIT_FRACTION))
        audit_sub = sub.sample(n=audit_n, replace=False, random_state=RANDOM_SEED + 1).copy()
        audit_sub["audit_round"] = "second_pass_audit"
        audit_frames.append(audit_sub)

    if audit_frames:
        pd.concat(audit_frames, ignore_index=True).sort_values(["repository", "commit_date"]).to_csv(PHASE2_AUDIT, index=False)

    pd.DataFrame([
        {"field": "annotator_1_label / annotator_label", "allowed_values": "include,exclude,uncertain", "definition": "include = true defect fix; exclude = non-defect event"},
        {"field": "inclusion_rule_triggered", "allowed_values": "incorrect_output,runtime_failure,semantic_violation,logic_fix,regression_fix", "definition": "Operational validation indicator map"},
        {"field": "exclusion_rule_triggered", "allowed_values": "refactor,docs,test_only,dependency_update,performance_only,style_only,build_only,ci_only,nonfunctional", "definition": "Exclusion mapping structure matching the annotation protocol"}
    ]).to_csv(PHASE2_GUIDE, index=False)

    pd.DataFrame([
        {
            "case_id": "REF_IN_01", "type": "INCLUDED", 
            "example_message": "Fix incorrect sign rotation matrix evaluation inside simulator kernel backend", 
            "rationale": "Corrects a structural deviation from intended runtime semantic output."
        },
        {
            "case_id": "REF_EX_01", "type": "EXCLUDED", 
            "example_message": "Refactor lint formatting layout changes across compiler package modules", 
            "rationale": "Non-functional structural maintenance adjustment; does not modify logic or correct runtime errors."
        }
    ]).to_csv(PHASE2_CASES, index=False)

    print("\nPhase 2 Complete. Manual validation structures deployed successfully.")

# ============================================================
# INTER-RATER AGREEMENT ANALYTICS
# ============================================================

def calculate_cohens_kappa(file_path: str) -> Optional[float]:
    if not os.path.exists(file_path):
        return None
    df = pd.read_csv(file_path)
    if "annotator_1_label" not in df.columns or "annotator_2_label" not in df.columns:
        return None
        
    df_clean = df.dropna(subset=["annotator_1_label", "annotator_2_label"])
    if df_clean.empty:
        return 0.0
        
    total = len(df_clean)
    categories = list(set(df_clean["annotator_1_label"].astype(str)).union(set(df_clean["annotator_2_label"].astype(str))))
    
    observed_matches = sum(df_clean["annotator_1_label"].astype(str) == df_clean["annotator_2_label"].astype(str))
    p_o = observed_matches / total
    
    p_e = 0.0
    for cat in categories:
        count1 = sum(df_clean["annotator_1_label"].astype(str) == cat)
        count2 = sum(df_clean["annotator_2_label"].astype(str) == cat)
        p_e += (count1 / total) * (count2 / total)
        
    if p_e == 1.0:
        return 1.0
        
    kappa = (p_o - p_e) / (1 - p_e)
    return kappa

# ============================================================
# INCLUSION MATRIX THRESHOLD REPORTING
# ============================================================

def generate_inclusion_threshold_report():
    if not os.path.exists(PHASE1_FINAL) or not os.path.exists(PHASE1_TAGS):
        return
        
    pool_df = pd.read_csv(PHASE1_FINAL)
    tags_df = pd.read_csv(PHASE1_TAGS)
    
    report_rows = []
    for repo_name in REPOS.keys():
        fix_count = len(pool_df[pool_df["repository"] == repo_name])
        release_count = len(tags_df[tags_df["repository"] == repo_name])
        
        meets_threshold = 1 if (fix_count >= 30 and release_count >= 5) else 0
        report_rows.append({
            "repository": repo_name,
            "validated_fixes_count": fix_count,
            "releases_in_window_count": release_count,
            "meets_srgm_threshold": meets_threshold
        })
        
    pd.DataFrame(report_rows).to_csv(PHASE3_THRESHOLDS, index=False)
    print("\nPhase 3 Pre-Flight Check Complete. Inclusion matrix thresholds written to output.")

# ============================================================
# SCIENTIFIC DEPOSITION ARCHIVER
# ============================================================

def phase3_archive_reproducibility_package():
    target_files = [
        PHASE1_FINAL, PHASE1_TAGS, PHASE2_SAMPLE, PHASE2_AUDIT,
        PHASE2_GUIDE, PHASE2_STATS, PHASE2_CASES, PHASE3_THRESHOLDS,
        SKIPPED_COMMITS_LOG, REPRODUCIBILITY_MANIFEST
    ]
    
    with zipfile.ZipFile(ZIP_ARCHIVE_OUTPUT, 'w', zipfile.ZIP_DEFLATED) as archive:
        for f_path in target_files:
            if os.path.exists(f_path):
                archive.write(f_path, os.path.basename(f_path))
    print(f"Unified Open Science Artifact Bundle Packaged: {ZIP_ARCHIVE_OUTPUT}")


if __name__ == "__main__":
    phase1_build_candidate_pool()
    phase2_generate_validation_samples()
    generate_inclusion_threshold_report()
    phase3_archive_reproducibility_package()