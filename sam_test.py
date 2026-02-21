import cv2
import torch
import numpy as np
import matplotlib.pyplot as plt
from segment_anything import sam_model_registry, SamAutomaticMaskGenerator

# ---- Load Image ----
image = cv2.imread("image.png")   # change to your image name
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

# ---- Load SAM Model ----
sam = sam_model_registry["vit_b"](checkpoint="sam_vit_b_01ec64.pth")
sam.to(device="cuda" if torch.cuda.is_available() else "cpu")

mask_generator = SamAutomaticMaskGenerator(sam)

# ---- Generate Masks ----
masks = mask_generator.generate(image)

print("Number of masks:", len(masks))

# ---- Visualize Masks ----
plt.figure(figsize=(10,10))
plt.imshow(image)

for mask in masks:
    m = mask["segmentation"]
    plt.imshow(np.dstack((m*255, np.zeros_like(m), np.zeros_like(m))), alpha=0.3)

plt.axis("off")
plt.show()