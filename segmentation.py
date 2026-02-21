import cv2
import numpy as np
import matplotlib.pyplot as plt


# ──────────────────────────────────────────────
# Stage-1 Validator
# ──────────────────────────────────────────────
class PlotValidatorStage1:

    def __init__(self, cultivated_mask, segmentation_confidence):
        self.mask = cultivated_mask        # bool array: True = green pixel
        self.confidence = segmentation_confidence  # 0.0 – 1.0

    def validate(self, polygon_mask):
        cultivated_inside = self.mask & polygon_mask

        plot_area      = polygon_mask.sum()
        cultivated_area = cultivated_inside.sum()

        percent_cultivated = cultivated_area / plot_area

        stage1_score = (
            0.7 * percent_cultivated +
            0.3 * self.confidence
        )

        decision = "PASS" if stage1_score > 0.65 else "REVIEW"

        return {
            "plot_exists_score":       percent_cultivated,
            "is_agricultural_score":   percent_cultivated,
            "stage1_validation_score": stage1_score,
            "decision":                decision,
        }


# ──────────────────────────────────────────────
# Load RGBA image (transparent PNG from data_pull.py)
# ──────────────────────────────────────────────
image_bgra = cv2.imread("image.png", cv2.IMREAD_UNCHANGED)

if image_bgra is None:
    print("ERROR: Could not load image.png")
    exit(1)

if image_bgra.shape[2] == 4:
    alpha     = image_bgra[:, :, 3]
    image_bgr = image_bgra[:, :, :3]
    plot_mask = alpha > 0               # True = inside polygon
else:
    image_bgr = image_bgra
    plot_mask = np.ones(image_bgra.shape[:2], dtype=bool)

image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
hsv       = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

# ──────────────────────────────────────────────
# Green / vegetation detection (HSV)
# ──────────────────────────────────────────────
lower_green = np.array([20,  20, 15])
upper_green = np.array([125, 255, 255])

green_mask_raw = cv2.inRange(hsv, lower_green, upper_green)
green_mask_raw[~plot_mask] = 0          # keep only inside polygon

kernel     = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
green_mask = cv2.morphologyEx(green_mask_raw, cv2.MORPH_CLOSE, kernel, iterations=3)
green_mask = cv2.morphologyEx(green_mask,     cv2.MORPH_OPEN,  kernel, iterations=2)

# ──────────────────────────────────────────────
# Segmentation confidence:
#   mean saturation of detected green pixels, normalised to 0-1
#   (higher S = more vivid / unambiguous green → higher confidence)
# ──────────────────────────────────────────────
detected = green_mask > 0
if detected.any():
    mean_sat = hsv[:, :, 1][detected].mean()   # S channel, 0-255
    segmentation_confidence = float(mean_sat / 255.0)
else:
    segmentation_confidence = 0.0

# ──────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────
plot_pixel_count  = int(np.sum(plot_mask))
green_pixel_count = int(np.sum(detected))
rest_pixel_count  = plot_pixel_count - green_pixel_count

print(f"Plot area:            {plot_pixel_count} px")
print(f"Green area:           {green_pixel_count} px  ({green_pixel_count/plot_pixel_count*100:.1f}%)")
print(f"Non-green area:       {rest_pixel_count} px  ({rest_pixel_count/plot_pixel_count*100:.1f}%)")
print(f"Segmentation conf.:   {segmentation_confidence:.3f}")

# ──────────────────────────────────────────────
# Stage-1 Validation
# ──────────────────────────────────────────────
validator = PlotValidatorStage1(
    cultivated_mask          = detected,
    segmentation_confidence  = segmentation_confidence,
)

result = validator.validate(polygon_mask=plot_mask)

print("\n── Stage-1 Validation ──────────────────────")
print(f"  Plot exists score:      {result['plot_exists_score']:.3f}")
print(f"  Is agricultural score:  {result['is_agricultural_score']:.3f}")
print(f"  Stage-1 score:          {result['stage1_validation_score']:.3f}")
print(f"  Decision:               {result['decision']}")
print("────────────────────────────────────────────")

# ──────────────────────────────────────────────
# Visualize
# ──────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(18, 6))
fig.suptitle(
    f"Stage-1: {result['decision']}  |  score={result['stage1_validation_score']:.3f}  "
    f"|  green={green_pixel_count/plot_pixel_count*100:.1f}%",
    fontsize=13, fontweight="bold"
)

display = image_rgb.copy()
display[~plot_mask] = 255

axes[0].imshow(display)
axes[0].set_title("Plot Image")
axes[0].axis("off")

axes[1].imshow(display)
green_overlay = np.zeros((*image_rgb.shape[:2], 4))
green_overlay[detected] = [0, 1, 0, 0.5]
axes[1].imshow(green_overlay)
axes[1].set_title("Green (Cultivated Land) Mask")
axes[1].axis("off")

extracted = image_rgb.copy()
extracted[~detected]  = 255
extracted[~plot_mask] = 255
axes[2].imshow(extracted)
axes[2].set_title("Extracted Green Regions")
axes[2].axis("off")

plt.tight_layout()
plt.show()

# ──────────────────────────────────────────────
# Save binary mask
# ──────────────────────────────────────────────
cv2.imwrite("green_mask.png", green_mask)
print("Saved green_mask.png")