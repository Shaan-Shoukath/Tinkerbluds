import cv2
import numpy as np
import matplotlib.pyplot as plt

# ---- Load Image ----
image = cv2.imread("image.png")
image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

# ---- Pixel-level green/vegetation detection using HSV ----
# OpenCV HSV: H [0-180], S [0-255], V [0-255]
# Satellite imagery forest canopy has H in ~90-120 (teal-green, not pure green)
# so we need a wide hue range: 20-125
lower_green = np.array([20, 20, 15])
upper_green = np.array([125, 255, 255])

green_mask = cv2.inRange(hsv, lower_green, upper_green)

# ---- Clean up with morphological operations ----
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
# Close small gaps inside green regions
green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
# Remove small noise blobs
green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel, iterations=2)

green_pixels = np.sum(green_mask > 0)
total_pixels = green_mask.shape[0] * green_mask.shape[1]
print(f"Green mask covers {green_pixels}/{total_pixels} pixels "
      f"({green_pixels/total_pixels*100:.1f}% of image)")

# ---- Visualize ----
fig, axes = plt.subplots(1, 3, figsize=(18, 6))

# Original image
axes[0].imshow(image_rgb)
axes[0].set_title("Original Image")
axes[0].axis("off")

# Green mask overlay on image
axes[1].imshow(image_rgb)
green_overlay = np.zeros((*image_rgb.shape[:2], 4))
green_overlay[green_mask > 0] = [0, 1, 0, 0.45]
axes[1].imshow(green_overlay)
axes[1].set_title("Green (Cultivated Land) Mask")
axes[1].axis("off")

# Masked-out result (only green regions)
masked_image = image_rgb.copy()
masked_image[green_mask == 0] = 0
axes[2].imshow(masked_image)
axes[2].set_title("Extracted Green Regions")
axes[2].axis("off")

plt.tight_layout()
plt.show()

# ---- Save ----
cv2.imwrite("green_mask.png", green_mask)
print("Saved green_mask.png")