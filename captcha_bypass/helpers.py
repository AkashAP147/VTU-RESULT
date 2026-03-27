"""
Helper utilities for image preprocessing used in CAPTCHA solving.
"""
import imutils
import cv2


def resize_to_fit(image, width, height):
    """
    Resize an image to fit within a given size while maintaining aspect ratio,
    then pad to exact dimensions.

    :param image: image to resize
    :param width: desired width in pixels
    :param height: desired height in pixels
    :return: the resized image
    """
    (h, w) = image.shape[:2]

    if w > h:
        image = imutils.resize(image, width=width)
    else:
        image = imutils.resize(image, height=height)

    padW = int((width - image.shape[1]) / 2.0)
    padH = int((height - image.shape[0]) / 2.0)

    image = cv2.copyMakeBorder(image, padH, padH, padW, padW, cv2.BORDER_REPLICATE)
    image = cv2.resize(image, (width, height))

    return image
