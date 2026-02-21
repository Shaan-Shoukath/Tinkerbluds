import cv2
import numpy as np
import matplotlib.pyplot as plt

# ---- Load RGBA image (transparent PNG from data_pull.py) ----
image_bgra = cv2.imread("image.png", cv2.IMREAD_UNCHANGED)

if image_bgra is None:
    print("ERROR: Could not load image.png")
    exit(1)

# Split alpha channel if present
if image_bgra.shape[2] == 4:
    alpha = image_bgra[:, :, 3]          # 0 = transparent, 255 = opaque
    image_bgr = image_bgra[:, :, :3]
    plot_mask = alpha > 0               # True = inside polygon
else:
    image_bgr = image_bgra
    plot_mask = np.ones(image_bgra.shape[:2], dtype=bool)

image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

# ---- Pixel-level green/vegetation detection using HSV ----
# OpenCV HSV: H [0-180], S [0-255], V [0-255]
# Satellite imagery forest canopy has H ~90-120 (teal-green, not pure green)
lower_green = np.array([20, 20, 15])
upper_green = np.array([125, 255, 255])

green_mask = cv2.inRange(hsv, lower_green, upper_green)

# ---- Only count green inside the plot polygon (exclude transparent area) ----
green_mask[~plot_mask] = 0

# ---- Clean up with morphological operations ----
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel, iterations=2)

# ---- Stats (only count pixels inside the plot) ----
plot_pixel_count  = np.sum(plot_mask)
green_pixel_count = np.sum(green_mask > 0)
rest_pixel_count  = plot_pixel_count - green_pixel_count
print(f"Plot area:      {plot_pixel_count} px")
print(f"Green area:     {green_pixel_count} px  ({green_pixel_count/plot_pixel_count*100:.1f}%)")
print(f"Non-green area: {rest_pixel_count} px  ({rest_pixel_count/plot_pixel_count*100:.1f}%)")

# ---- Visualize ----
fig, axes = plt.subplots(1, 3, figsize=(18, 6))

# Original image (show transparent as white)
display = image_rgb.copy()
display[~plot_mask] = 255
axes[0].imshow(display)
axes[0].set_title("Plot Image")
axes[0].axis("off")

# Green mask overlay
axes[1].imshow(display)
green_overlay = np.zeros((*image_rgb.shape[:2], 4))
green_overlay[green_mask > 0] = [0, 1, 0, 0.5]
axes[1].imshow(green_overlay)
axes[1].set_title("Green (Cultivated Land) Mask")
axes[1].axis("off")

# Extracted green regions
extracted = image_rgb.copy()
extracted[green_mask == 0] = 255   # white outside green
extracted[~plot_mask] = 255        # white outside polygon
axes[2].imshow(extracted)
axes[2].set_title("Extracted Green Regions")
axes[2].axis("off")

plt.tight_layout()
plt.show()

# ---- Save binary green mask ----
cv2.imwrite("green_mask.png", green_mask)
print("Saved green_mask.png")