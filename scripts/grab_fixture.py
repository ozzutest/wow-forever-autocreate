import sys, time
import cv2

from pathlib import Path
from vision.capture import make_capture

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

def main(state: str, delay: float = 3.0, mask: str | None = None) -> None:
    time.sleep(delay)                      
    frame = make_capture("World of Warcraft").capture()
    if mask:                               
        x, y, w, h = map(int, mask.split(","))
        frame[y:y + h, x:x + w] = 0
    n = len(list(FIXTURES.glob(f"{state}_*.png"))) + 1
    out = FIXTURES / f"{state}_{n:02}.png"
    cv2.imwrite(str(out), frame)
    print(f"saved {out.name} {frame.shape[1]}x{frame.shape[0]}")

if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3] and [float(sys.argv[2])]), *(sys.argv[3:4]))