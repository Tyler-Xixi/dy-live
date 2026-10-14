"""Shared rounded scrollbar for client windows."""
import tkinter as tk

class SmoothScrollbar(tk.Canvas):
    """Slim rounded thumb, immediate dragging, stable gutter when unused."""
    def __init__(self, master, command, on_interact=None, **kwargs):
        scale = max(1, float(master.tk.call('tk', 'scaling')) / 1.3333)
        super().__init__(master, width=round(14*scale), highlightthickness=0,
                         background='#f4f6fa', takefocus=True, **kwargs)
        self.command = command
        self.on_interact = on_interact
        self.first, self.last = 0., 1.
        self.hover = False
        self.drag_offset = None
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<Enter>', lambda _: self.set_hover(True))
        self.bind('<Leave>', lambda _: self.set_hover(False))
        self.bind('<Button-1>', self.press)
        self.bind('<B1-Motion>', self.drag)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Up>', lambda _: self.scroll(-1))
        self.bind('<Down>', lambda _: self.scroll(1))
        self.bind('<Prior>', lambda _: self.scroll(-1, 'pages'))
        self.bind('<Next>', lambda _: self.scroll(1, 'pages'))

    def set(self, first, last):
        self.first = max(0., min(1., float(first)))
        self.last = max(self.first, min(1., float(last)))
        self.draw()

    def thumb_bounds(self):
        track = max(1, self.winfo_height()-8)
        span = self.last-self.first
        length = min(track, max(28, track*span))
        top = 4 + (track-length)*self.first / max(.000001, 1-span)
        return top, top+length, track

    def draw(self):
        self.delete('all')
        if self.last-self.first >= .999:
            return
        top, bottom, _ = self.thumb_bounds()
        width = 8 if self.hover or self.drag_offset is not None else 6
        self.create_line(self.winfo_width()/2, top+width/2,
                         self.winfo_width()/2, bottom-width/2,
                         width=width, capstyle='round',
                         fill='#809bc0' if self.hover or self.drag_offset is not None else '#becada')

    def set_hover(self, hover):
        self.hover = hover
        self.configure(cursor='hand2' if hover else '')
        self.draw()

    def press(self, event):
        if self.last-self.first >= .999:
            return
        self.focus_set()
        if self.on_interact:
            self.on_interact()
        top, bottom, _ = self.thumb_bounds()
        self.drag_offset = event.y-top if top <= event.y <= bottom else (bottom-top)/2
        self.drag(event)

    def drag(self, event):
        if self.drag_offset is None:
            return
        top, bottom, track = self.thumb_bounds()
        position = max(0., min(1., (event.y-4-self.drag_offset)/max(1, track-(bottom-top))))
        self.command('moveto', position*(1-(self.last-self.first)))
        self.draw()

    def release(self, _):
        self.drag_offset = None
        self.draw()

    def scroll(self, direction, units='units'):
        if self.on_interact:
            self.on_interact()
        self.command('scroll', direction, units)
        return 'break'
