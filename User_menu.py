"""
Erdemtugs Odsuren --- 10/15/2026.
"""

import argparse
import glob
import os
import queue
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

# used to find the .egg file to install carla Lib
try:
    sys.path.append(glob.glob(os.path.join(HERE, '..', 'carla', 'dist', 'carla-*%d.%d-%s.egg' % (
        sys.version_info.major,
        sys.version_info.minor,
        'win-amd64' if os.name == 'nt' else 'linux-x86_64')))[0])
except IndexError:
    pass

CARLA_IMPORT_ERROR = None
try:
    import carla
except ImportError as exc:
    carla = None
    CARLA_IMPORT_ERROR = str(exc)

import pygame

MAP_PREFIX = '/Game/Carla/Maps/'
RESOLUTIONS = ['320x180', '640x360', '800x450', '1280x720']
CONNECT_TIMEOUT = 120.0      # seconds; a slow laptop needs patience
LOAD_MAP_TIMEOUT = 300.0     # seconds
RANDOM_CAR = 'vehicle.*'

WIDTH, HEIGHT = 820, 640
BG = (24, 26, 31)
PANEL = (34, 37, 44)
TEXT = (225, 228, 235)
DIM = (140, 146, 158)
SELECTED = (48, 84, 140)
BUTTON = (58, 63, 75)
BUTTON_HOVER = (74, 80, 94)
BUTTON_OFF = (36, 38, 44)
GO = (46, 125, 80)
STOP = (170, 60, 60)
OK = (110, 200, 140)


def short_map_name(path):
    return path[len(MAP_PREFIX):] if path.startswith(MAP_PREFIX) else path


def base_name(path):
    return path.replace('\\', '/').split('/')[-1]


def wrap(text, font, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split(' '):
            candidate = (line + ' ' + word).strip()
            if font.size(candidate)[0] <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


class Button(object):
    def __init__(self, rect, label, action):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.action = action
        self.enabled = True
        self.color = None

    def draw(self, surface, font, mouse):
        if not self.enabled:
            fill = BUTTON_OFF
        elif self.color is not None:
            fill = self.color
        elif self.rect.collidepoint(mouse):
            fill = BUTTON_HOVER
        else:
            fill = BUTTON
        pygame.draw.rect(surface, fill, self.rect, border_radius=6)
        text = font.render(self.label, True, TEXT if self.enabled else DIM)
        surface.blit(text, text.get_rect(center=self.rect.center))


class ListView(object):
    def __init__(self, rect, row_h=24):
        self.rect = pygame.Rect(rect)
        self.row_h = row_h
        self.items = []
        self.selected = -1
        self.top = 0

    @property
    def rows(self):
        return max(1, self.rect.height // self.row_h)

    def _clamp(self):
        self.top = max(0, min(max(0, len(self.items) - self.rows), self.top))

    def set_items(self, items, selected=-1):
        self.items = list(items)
        self.selected = selected
        self.top = 0
        self.show_selected()

    def show_selected(self):
        if self.selected >= 0:
            if self.selected < self.top:
                self.top = self.selected
            elif self.selected >= self.top + self.rows:
                self.top = self.selected - self.rows + 1
        self._clamp()

    def scroll(self, amount):
        self.top -= amount * 3
        self._clamp()

    def index_at(self, pos):
        if not self.rect.collidepoint(pos):
            return None
        index = self.top + (pos[1] - self.rect.y) // self.row_h
        return index if 0 <= index < len(self.items) else None

    def draw(self, surface, font):
        pygame.draw.rect(surface, PANEL, self.rect, border_radius=6)
        surface.set_clip(self.rect.inflate(-6, -4))
        for row in range(self.rows):
            index = self.top + row
            if index >= len(self.items):
                break
            row_rect = pygame.Rect(self.rect.x + 4, self.rect.y + 2 + row * self.row_h,
                                   self.rect.width - 16, self.row_h)
            if index == self.selected:
                pygame.draw.rect(surface, SELECTED, row_rect, border_radius=4)
            text = font.render(self.items[index], True, TEXT)
            surface.blit(text, (row_rect.x + 6, row_rect.y + (self.row_h - text.get_height()) // 2))
        surface.set_clip(None)
        if len(self.items) > self.rows:                       # scrollbar
            track = pygame.Rect(self.rect.right - 8, self.rect.y + 4, 4, self.rect.height - 8)
            thumb_h = max(20, int(track.height * self.rows / float(len(self.items))))
            span = track.height - thumb_h
            maxtop = max(1, len(self.items) - self.rows)
            thumb = pygame.Rect(track.x, track.y + int(span * self.top / float(maxtop)), 4, thumb_h)
            pygame.draw.rect(surface, DIM, thumb, border_radius=2)


class App(object):
    def __init__(self, host, port):
        pygame.init()
        pygame.display.set_caption('CARLA launcher')
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.font = pygame.font.SysFont('segoeui,arial,dejavusans', 17)
        self.small = pygame.font.SysFont('segoeui,arial,dejavusans', 15)
        self.big = pygame.font.SysFont('segoeui,arial,dejavusans', 21, bold=True)

        self.host = host
        self.port = port
        self.client = None
        self.proc = None
        self.busy = False
        self.running = True
        self.maps = []
        self.all_cars = []
        self.shown_cars = [RANDOM_CAR]
        self.current_map = None
        self.search = ''
        self.four_wheels = True
        self.scripts = self._find_scripts()
        self.script_index = 0
        self.res_index = 1
        self.status = ''
        self.results = queue.Queue()
        self.last_click = (None, 0)

        self.map_list = ListView((10, 92, 395, 380))
        self.car_list = ListView((415, 124, 395, 348))
        self.connect_btn = Button((650, 10, 160, 32), 'Connect / refresh', self.connect)
        self.load_btn = Button((10, 482, 395, 34), 'Load selected map', self.load_selected_map)
        self.wheels_btn = Button((415, 482, 395, 34), '', self.toggle_wheels)
        self.script_btn = Button((10, 526, 395, 34), '', self.cycle_script)
        self.res_btn = Button((415, 526, 190, 34), '', self.cycle_res)
        self.start_btn = Button((620, 526, 190, 34), 'Start driving', self.toggle_drive)
        self.buttons = [self.connect_btn, self.load_btn, self.wheels_btn,
                        self.script_btn, self.res_btn, self.start_btn]
        self.refresh_cars()

        if carla is None:
            self.status = ('Could not import carla (%s). Run this from PythonAPI\\examples with the '
                           'same Python you use for manual_control.py.' % CARLA_IMPORT_ERROR)
        else:
            self.connect()

    # -- utilities --

    def _find_scripts(self):
        names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, 'manual_control*.py')))
        names.sort(key=lambda n: (n != 'manual_control.py', n))
        return names

    def set_status(self, text):
        self.status = text

    def run_bg(self, label, work, done):
        if self.busy:
            return
        self.busy = True
        self.set_status(label)

        def target():
            try:
                outcome = (True, work())
            except Exception as exc:
                outcome = (False, exc)
            self.results.put((done, outcome))

        threading.Thread(target=target, daemon=True).start()

    def poll(self):
        try:
            while True:
                done, (ok, value) = self.results.get_nowait()
                self.busy = False
                done(ok, value)
        except queue.Empty:
            pass
        self._check_proc()

    #-- connection --

    def connect(self):
        if carla is None or self.busy:
            return
        host, port = self.host, self.port

        def work():
            client = carla.Client(host, port)
            client.set_timeout(CONNECT_TIMEOUT)
            world = client.get_world()
            maps = sorted(set(short_map_name(m) for m in client.get_available_maps()))
            current = base_name(world.get_map().name)
            cars = []
            for bp in world.get_blueprint_library().filter('vehicle.*'):
                wheels = 4
                if bp.has_attribute('number_of_wheels'):
                    wheels = bp.get_attribute('number_of_wheels').as_int()
                cars.append((bp.id, wheels))
            return client, maps, current, sorted(cars)

        def done(ok, value):
            if not ok:
                self.client = None
                self.set_status('Could not reach the server at %s:%d: %s\nIs CARLA running and fully '
                                'loaded? (tasklist | findstr /I carla)' % (host, port, value))
                return
            self.client, self.maps, self.current_map, self.all_cars = value
            selected = -1
            for index, name in enumerate(self.maps):
                if base_name(name) == self.current_map:
                    selected = index
            self.map_list.set_items(self.maps, selected)
            self.refresh_cars()
            self.set_status('Connected: %d maps, %d cars.' % (len(self.maps), len(self.all_cars)))

        self.run_bg('Connecting to %s:%d ... ' % (host, port), work, done)

    #--- maps --

    def load_selected_map(self):
        index = self.map_list.selected
        if self.busy or self.client is None or not (0 <= index < len(self.maps)):
            self.set_status('Select a map in the list first.')
            return
        name = self.maps[index]
        if self.proc is not None:
            self.stop_drive(cleanup=False)
        client = self.client

        def work():
            client.set_timeout(LOAD_MAP_TIMEOUT)
            try:
                client.load_world(name)
                return base_name(client.get_world().get_map().name)
            finally:
                client.set_timeout(CONNECT_TIMEOUT)

        def done(ok, value):
            if ok:
                self.current_map = value
                self.set_status('Loaded %s. Press Start driving to spawn a car on it.' % value)
            else:
                self.set_status('Map load failed or timed out: %s\nThe server may still be loading: '
                                'wait a minute, then press Connect / refresh.' % value)

        self.run_bg('Loading %s ... this can take a minute, do not click anything.' % name, work, done)

    #-- vroom --

    def refresh_cars(self):
        text = self.search.strip().lower()
        self.shown_cars = [RANDOM_CAR]
        labels = ['Random car']
        for car_id, wheels in self.all_cars:
            if self.four_wheels and wheels != 4:
                continue
            if text and text not in car_id.lower():
                continue
            self.shown_cars.append(car_id)
            labels.append(car_id)
        self.car_list.set_items(labels, 0)

    def selected_car(self):
        index = self.car_list.selected
        return self.shown_cars[index] if 0 <= index < len(self.shown_cars) else RANDOM_CAR

    def toggle_wheels(self):
        self.four_wheels = not self.four_wheels
        self.refresh_cars()

    def cycle_script(self):
        if self.scripts:
            self.script_index = (self.script_index + 1) % len(self.scripts)

    def cycle_res(self):
        self.res_index = (self.res_index + 1) % len(RESOLUTIONS)

    #-- driving --

    def toggle_drive(self):
        if self.proc is not None:
            self.stop_drive()
        else:
            self.start_drive()

    def start_drive(self):
        if not self.scripts:
            self.set_status('No manual_control*.py found next to the launcher (%s).' % HERE)
            return
        name = self.scripts[self.script_index]
        script = os.path.join(HERE, name)
        car = self.selected_car()
        cmd = [sys.executable, script, '--host', self.host, '--port', str(self.port),
               '--filter', car, '--res', RESOLUTIONS[self.res_index]]
        try:
            with open(script, 'r') as handle:
                if '--generation' in handle.read():
                    cmd += ['--generation', 'all']
        except (IOError, OSError):
            pass
        self.proc = subprocess.Popen(cmd, cwd=HERE)
        self.set_status('Started %s with %s. Output and errors appear in the terminal you launched '
                        'this from.' % (name, 'a random car' if car == RANDOM_CAR else car))

    def stop_drive(self, cleanup=True):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except OSError:
            pass
        self.proc = None
        self.set_status('Stopped the driving script.')
        if cleanup and self.client is not None:
            self._cleanup_hero()

    def _cleanup_hero(self):
        client = self.client

        def work():
            world = client.get_world()
            victims = []
            for vehicle in world.get_actors().filter('vehicle.*'):
                if vehicle.attributes.get('role_name') == 'hero':
                    for sensor in world.get_actors().filter('sensor.*'):
                        parent = sensor.parent
                        if parent is not None and parent.id == vehicle.id:
                            victims.append(sensor)
                    victims.append(vehicle)
            client.apply_batch([carla.command.DestroyActor(actor) for actor in victims])
            return len(victims)

        def done(ok, value):
            if ok:
                self.set_status('Stopped. Removed %d leftover actor(s) from the server.' % value)
            else:
                self.set_status('Stopped, but cleanup failed: %s' % value)

        self.run_bg('Cleaning up the old car ...', work, done)

    def _check_proc(self):
        if self.proc is None:
            return
        code = self.proc.poll()
        if code is None:
            return
        self.proc = None
        if code == 0:
            self.set_status('The driving script closed.')
        else:
            self.set_status('The driving script exited with code %s. Check the terminal for the error '
                            '(a crashed server needs a restart).' % code)

    #-- events --

    def click(self, pos):
        for button in self.buttons:
            if button.enabled and button.rect.collidepoint(pos):
                button.action()
                return
        index = self.map_list.index_at(pos)
        if index is not None:
            double = (self.last_click[0] == ('map', index) and
                      pygame.time.get_ticks() - self.last_click[1] < 400)
            self.map_list.selected = index
            self.last_click = (('map', index), pygame.time.get_ticks())
            if double:
                self.load_selected_map()
            return
        index = self.car_list.index_at(pos)
        if index is not None:
            self.car_list.selected = index
            self.last_click = (('car', index), pygame.time.get_ticks())

    def handle(self, event):
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.click(event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            pos = pygame.mouse.get_pos()
            if self.map_list.rect.collidepoint(pos):
                self.map_list.scroll(event.y)
            elif self.car_list.rect.collidepoint(pos):
                self.car_list.scroll(event.y)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.search = ''
                self.refresh_cars()
            elif event.key == pygame.K_BACKSPACE:
                self.search = self.search[:-1]
                self.refresh_cars()
            elif event.unicode and event.unicode.isprintable():
                self.search += event.unicode
                self.refresh_cars()

    #-- drawing --

    def draw(self):
        screen = self.screen
        screen.fill(BG)
        mouse = pygame.mouse.get_pos()

        screen.blit(self.big.render('CARLA launcher', True, TEXT), (12, 12))
        server = '%s:%d  |  map: %s' % (self.host, self.port, self.current_map or 'not connected')
        screen.blit(self.small.render(server, True, OK if self.client is not None else DIM), (12, 46))

        screen.blit(self.font.render('Maps  (double-click to load)', True, DIM), (12, 68))
        screen.blit(self.font.render('Cars', True, DIM), (417, 68))

        search_rect = pygame.Rect(415, 90, 395, 28)
        pygame.draw.rect(screen, PANEL, search_rect, border_radius=6)
        if self.search:
            label, color = self.search, TEXT
        else:
            label, color = 'type to search cars...', DIM
        screen.blit(self.font.render(label, True, color), (search_rect.x + 8, search_rect.y + 4))

        self.map_list.draw(screen, self.font)
        self.car_list.draw(screen, self.font)

        self.connect_btn.enabled = not self.busy
        self.load_btn.enabled = (not self.busy) and self.client is not None
        self.wheels_btn.label = '4 wheels only: %s' % ('ON' if self.four_wheels else 'OFF')
        self.script_btn.label = 'Script: %s  (click to change)' % (
            self.scripts[self.script_index] if self.scripts else 'none found')
        self.script_btn.enabled = bool(self.scripts)
        self.res_btn.label = 'Window: %s' % RESOLUTIONS[self.res_index]
        self.start_btn.label = 'Stop driving' if self.proc is not None else 'Start driving'
        self.start_btn.color = STOP if self.proc is not None else GO
        self.start_btn.enabled = (not self.busy) and (self.client is not None or self.proc is not None)
        for button in self.buttons:
            button.draw(screen, self.font, mouse)

        y = 572
        for line in wrap(self.status, self.small, WIDTH - 24)[:4]:
            screen.blit(self.small.render(line, True, TEXT), (12, y))
            y += 18
        if self.busy:
            screen.blit(self.small.render('working ...', True, DIM), (WIDTH - 100, 46))

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                self.handle(event)
            self.poll()
            self.draw()
            pygame.display.flip()
            clock.tick(30)
        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description='CARLA map and car launcher')
    parser.add_argument('--host', default='127.0.0.1', help='server address (default 127.0.0.1)')
    parser.add_argument('-p', '--port', default=2000, type=int, help='server port (default 2000)')
    args = parser.parse_args()
    App(args.host, args.port).run()


if __name__ == '__main__':
    main()