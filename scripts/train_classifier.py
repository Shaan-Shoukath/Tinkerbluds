"""
train_classifier.py — Bootstrap training script for the crop classifier.

Phase 1: Uses existing threshold-based labels as ground truth.
Phase 2: Will retrain on real user confirmations.

Usage:
    python scripts/train_classifier.py --data training_data.csv --output models/crop_classifier.json
"""

import argparse
import json
import os
import sys

# Add parent dir to path so we can import from plot_validation
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def generate_bootstrap_data(n_samples: int = 500) -> list:
    """
    Generate synthetic training data using threshold-based labels.

    In production, replace this with real data collected from validated plots.
    """
    import random

    random.seed(42)
    data = []

    for _ in range(n_samples):
        # Simulate cropland plots (label = 1)
        if random.random() < 0.6:
            features = {
                "ndvi_mean": random.uniform(0.3, 0.9),
                "ndvi_stddev": random.uniform(0.05, 0.25),
                "vh_mean_db": random.uniform(-15, -8),
                "vh_vv_ratio": random.uniform(0.35, 0.7),
                "elevation_m": random.uniform(0, 500),
                "slope_deg": random.uniform(0, 10),
                "rainfall_mm": random.uniform(800, 3000),
                "soil_moisture": random.uniform(0.15, 0.45),
            }
            label = 1
        else:
            # Simulate non-cropland (forest, urban, water)
            terrain_type = random.choice(["forest", "urban", "water"])
            if terrain_type == "forest":
                features = {
                    "ndvi_mean": random.uniform(0.5, 0.95),
                    "ndvi_stddev": random.uniform(0.01, 0.05),
                    "vh_mean_db": random.uniform(-18, -12),
                    "vh_vv_ratio": random.uniform(0.15, 0.35),
                    "elevation_m": random.uniform(200, 1500),
                    "slope_deg": random.uniform(5, 30),
                    "rainfall_mm": random.uniform(1500, 4000),
                    "soil_moisture": random.uniform(0.2, 0.5),
                }
            elif terrain_type == "urban":
                features = {
                    "ndvi_mean": random.uniform(0.0, 0.2),
                    "ndvi_stddev": random.uniform(0.01, 0.05),
                    "vh_mean_db": random.uniform(-12, -5),
                    "vh_vv_ratio": random.uniform(0.4, 0.8),
                    "elevation_m": random.uniform(0, 300),
                    "slope_deg": random.uniform(0, 5),
                    "rainfall_mm": random.uniform(500, 2000),
                    "soil_moisture": random.uniform(0.05, 0.2),
                }
            else:  # water
                features = {
                    "ndvi_mean": random.uniform(-0.3, 0.1),
                    "ndvi_stddev": random.uniform(0.01, 0.03),
                    "vh_mean_db": random.uniform(-25, -18),
                    "vh_vv_ratio": random.uniform(0.05, 0.2),
                    "elevation_m": random.uniform(0, 50),
                    "slope_deg": random.uniform(0, 2),
                    "rainfall_mm": random.uniform(1000, 3000),
                    "soil_moisture": random.uniform(0.4, 0.5),
                }
            label = 0

        data.append({"features": features, "label": label})

    return data


def train_model(data: list, output_path: str):
    """Train XGBoost model on the provided data."""
    try:
        import numpy as np
        import xgboost as xgb
    except ImportError:
        print("ERROR: xgboost and numpy are required. Install with:")
        print("  pip install xgboost numpy")
        sys.exit(1)

    from plot_validation.ml_classifier import FEATURE_NAMES

    # Build arrays
    X = np.array([[d["features"].get(f, 0.0) for f in FEATURE_NAMES] for d in data])
    y = np.array([d["label"] for d in data])

    # Split 80/20
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_NAMES)
    dtest = xgb.DMatrix(X_test, label=y_test, feature_names=FEATURE_NAMES)

    # Train
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 4,
        "learning_rate": 0.1,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "seed": 42,
    }

    model = xgb.train(
        params,
        dtrain,
        num_boost_round=100,
        evals=[(dtrain, "train"), (dtest, "test")],
        early_stopping_rounds=10,
        verbose_eval=10,
    )

    # Evaluate
    preds = model.predict(dtest)
    accuracy = sum((preds > 0.5) == y_test) / len(y_test)
    print(f"\nTest accuracy: {accuracy:.1%}")

    # Feature importance
    importance = model.get_score(importance_type="gain")
    total = sum(importance.values())
    print("\nFeature importance:")
    for feat, score in sorted(importance.items(), key=lambda x: -x[1]):
        print(f"  {feat}: {score/total:.1%}")

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    model.save_model(output_path)
    print(f"\nModel saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Train crop classifier")
    parser.add_argument("--data", help="Path to training CSV (optional, uses bootstrap if absent)")
    parser.add_argument("--output", default="data/crop_classifier.json", help="Model output path")
    parser.add_argument("--samples", type=int, default=500, help="Bootstrap sample count")
    args = parser.parse_args()

    if args.data and os.path.exists(args.data):
        print(f"Loading training data from {args.data}...")
        with open(args.data) as f:
            data = json.load(f)
    else:
        print(f"Generating {args.samples} bootstrap training samples...")
        data = generate_bootstrap_data(args.samples)

    print(f"Training on {len(data)} samples...")
    train_model(data, args.output)


if __name__ == "__main__":
    main()
