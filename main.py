import os
import re
import time
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

VIDEOS_ROOT = "Videos"
PREVIEW_W = 480          # širina preview/snimke
PREVIEW_H = 360          # visina preview/snimke  -> omjer 3:4 (360:480 = 3:4)
TARGET_FPS = 30          # ciljani FPS za video
GUI_REFRESH_MS = 15      # osvježavanje GUI-ja u milisekundama (~66 FPS)
COUNTDOWN_SECONDS = 3
CAMERA_INDEX = 0


class CameraApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Snimanje videa")
        self.root.geometry("880x560")
        self.root.resizable(False, False)

        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        
        # Postavi kameru na željeni FPS
        self.cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        print("Camera FPS setting:", self.cap.get(cv2.CAP_PROP_FPS))

        if not self.cap.isOpened():
            messagebox.showerror("Greška", "Ne mogu pristupiti kameri (index %d)." % CAMERA_INDEX)

        # stanje aplikacije: idle, countdown, recording, finished
        self.state = "idle"
        self.video_name = ""
        self.total_videos = 0
        self.current_video_num = 0     # redni broj u ovoj sesiji (0-based)
        self.next_file_index = 0       # broj za naziv datoteke (npr. 0007)
        self.folder_path = ""
        self.writer = None
        self.record_start_time = None
        self.countdown_start_time = None
        
        # Varijable za sinkronizaciju frame-ova
        self.video_frame_count = 0
        self.video_start_time = None
        self.last_frame_time = None
        self.fps_measurements = []

        self._build_gui()
        self._update_frame()

    # ---------------- GUI ----------------
    def _build_gui(self):
        main = tk.Frame(self.root)
        main.pack(fill="both", expand=True)

        # Lijevi panel
        left = tk.Frame(main, width=260, padx=20, pady=20)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Naziv videa:", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 5))
        self.name_entry = tk.Entry(left, font=("Segoe UI", 11))
        self.name_entry.pack(fill="x", pady=(0, 15))

        tk.Label(left, text="Broj videa za snimiti:", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 5))
        self.count_entry = tk.Entry(left, font=("Segoe UI", 11))
        self.count_entry.insert(0, "1")
        self.count_entry.pack(fill="x", pady=(0, 15))

        self.start_btn = tk.Button(
            left, text="Počni snimanje", font=("Segoe UI", 12, "bold"),
            bg="#4CAF50", fg="white", activebackground="#43a047",
            command=self.start_session, cursor="hand2"
        )
        self.start_btn.pack(fill="x", ipady=6, pady=(10, 0))

        self.status_label = tk.Label(
            left, text="", font=("Segoe UI", 10), fg="#555",
            justify="left", wraplength=220
        )
        self.status_label.pack(anchor="w", pady=(20, 0))

        # Desni panel - preview
        right = tk.Frame(main, padx=20, pady=20)
        right.pack(side="right", fill="both", expand=True)

        self.canvas = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, bg="black", highlightthickness=1,
                                 highlightbackground="#888")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)

        hint = tk.Label(right, text="Lijevi klik na preview prekida trenutno snimanje.",
                         font=("Segoe UI", 9), fg="#777")
        hint.pack(pady=(8, 0))

    # ---------------- Logika numeracije ----------------
    def _get_next_index(self, folder):
        if not os.path.isdir(folder):
            return 0
        pattern = re.compile(r"^(\d{4})\.mp4$")
        max_idx = -1
        for fname in os.listdir(folder):
            m = pattern.match(fname)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        return max_idx + 1

    # ---------------- Upravljanje sesijom ----------------
    def start_session(self):
        if self.state not in ("idle", "finished"):
            return

        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Upozorenje", "Unesite naziv videa.")
            return

        try:
            count = int(self.count_entry.get().strip())
            if count <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Upozorenje", "Unesite ispravan broj videa (cijeli broj veći od 0).")
            return

        self.video_name = name
        self.total_videos = count
        self.folder_path = os.path.join(VIDEOS_ROOT, name)
        os.makedirs(self.folder_path, exist_ok=True)
        self.next_file_index = self._get_next_index(self.folder_path)
        self.current_video_num = 0

        self.start_btn.config(state="disabled")
        self.name_entry.config(state="disabled")
        self.count_entry.config(state="disabled")

        self._begin_countdown()

    def _begin_countdown(self):
        self.state = "countdown"
        self.countdown_start_time = time.time()
        self.status_label.config(
            text=f"Priprema za video {self.current_video_num + 1}/{self.total_videos}..."
        )

    def _start_recording(self):
        self.state = "recording"
        fname = f"{self.next_file_index:04d}.mp4"
        full_path = os.path.join(self.folder_path, fname)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        
        # Postavi VideoWriter s TARGET_FPS
        self.writer = cv2.VideoWriter(full_path, fourcc, TARGET_FPS, (PREVIEW_W, PREVIEW_H))
        
        # Resetiraj brojače za sinkronizaciju
        self.video_frame_count = 0
        self.video_start_time = time.time()
        self.record_start_time = time.time()
        
        self.status_label.config(
            text=f"Snima se: {fname}\n(lijevi klik za prekid)"
        )

    def _stop_recording(self):
        if self.writer is not None:
            # Osiguraj da video ima točno trajanje - dodaj prazne frame-ove ako treba
            elapsed = time.time() - self.video_start_time
            expected_frames = int(elapsed * TARGET_FPS)
            
            # Ako fali frame-ova, dupliciraj zadnji frame
            while self.video_frame_count < expected_frames:
                # Ne možemo dodati zadnji frame jer ga nemamo spremljenog,
                # pa ćemo jednostavno pustiti video da bude malo kraći
                break
            
            self.writer.release()
            self.writer = None

        self.current_video_num += 1
        if self.current_video_num >= self.total_videos:
            self._finish_session()
        else:
            self.next_file_index += 1
            self._begin_countdown()

    def _finish_session(self):
        self.state = "finished"
        self.status_label.config(
            text=f"Gotovo! Snimljeno {self.total_videos} video(a)\nu folderu '{self.folder_path}'."
        )
        self.start_btn.config(state="normal")
        self.name_entry.config(state="normal")
        self.count_entry.config(state="normal")

    def _on_click(self, event):
        if self.state == "recording":
            self._stop_recording()

    # ---------------- Obrada slike ----------------
    def _crop_to_ratio(self, frame):
        h, w = frame.shape[:2]
        target_ratio = PREVIEW_W / PREVIEW_H  # 0.75 (3:4)
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            x0 = (w - new_w) // 2
            frame = frame[:, x0:x0 + new_w]
        else:
            new_h = int(w / target_ratio)
            y0 = (h - new_h) // 2
            frame = frame[y0:y0 + new_h, :]
        return cv2.resize(frame, (PREVIEW_W, PREVIEW_H))

    def _draw_countdown(self, frame, number):
        text = str(number)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thickness = 4, 8
        size = cv2.getTextSize(text, font, scale, thickness)[0]
        x = (PREVIEW_W - size[0]) // 2
        y = (PREVIEW_H + size[1]) // 2
        cv2.putText(frame, text, (x + 2, y + 2), font, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
        cv2.putText(frame, text, (x, y), font, scale, (0, 255, 0), thickness, cv2.LINE_AA)

    def _draw_recording_info(self, frame):
        elapsed = time.time() - self.record_start_time
        mins, secs = int(elapsed // 60), int(elapsed % 60)
        text1 = f"Video {self.current_video_num + 1}/{self.total_videos}"
        text2 = f"{mins:02d}:{secs:02d}"

        for text, y in ((text1, 25), (text2, 50)):
            cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2, cv2.LINE_AA)

        cv2.circle(frame, (PREVIEW_W - 20, 18), 8, (0, 0, 255), -1)

    # ---------------- Glavna petlja ----------------
    def _update_frame(self):
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.flip(frame, 1)
            frame = self._crop_to_ratio(frame)

            if self.state == "countdown":
                elapsed = time.time() - self.countdown_start_time
                remaining = COUNTDOWN_SECONDS - int(elapsed)
                if remaining <= 0:
                    self._start_recording()
                else:
                    self._draw_countdown(frame, remaining)

            if self.state == "recording":
                if self.writer is not None:
                    current_time = time.time()
                    
                    # Sinkronizacija: koliko frame-ova treba biti zapisano do sada
                    elapsed = current_time - self.video_start_time
                    expected_frames = int(elapsed * TARGET_FPS)
                    
                    # Piši frame onoliko puta koliko je potrebno
                    while self.video_frame_count < expected_frames:
                        self.writer.write(frame)
                        self.video_frame_count += 1
                    
                    self.last_frame_time = current_time
                    
                self._draw_recording_info(frame)

            # Mjerenje stvarnog FPS-a kamere (samo za debug)
            now = time.time()
            if hasattr(self, 'last_fps_time'):
                self.fps_measurements.append(now - self.last_fps_time)
                if len(self.fps_measurements) > 30:
                    self.fps_measurements.pop(0)
                if len(self.fps_measurements) > 0 and now - self.last_fps_print >= 2.0:
                    avg_fps = 1.0 / (sum(self.fps_measurements) / len(self.fps_measurements))
                    print(f"Stvarni FPS kamere: {avg_fps:.1f}")
                    self.last_fps_print = now
            else:
                self.last_fps_time = now
                self.last_fps_print = now

            # Prikaz u GUI-ju
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.canvas.imgtk = imgtk  # čuvamo referencu da je garbage collector ne obriše
            self.canvas.create_image(0, 0, anchor="nw", image=imgtk)

        # Koristi fiksno osvježavanje GUI-ja, ne ovisno o FPS-u
        self.root.after(GUI_REFRESH_MS, self._update_frame)

    def on_close(self):
        if self.writer is not None:
            self.writer.release()
        if self.cap is not None:
            self.cap.release()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = CameraApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()