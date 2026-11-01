"""Local full-speed A/B playback with one original-audio clock and shared viewing."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                              QPushButton, QSlider, QVBoxLayout, QWidget)


class ComparisonCanvas(QWidget):
    def __init__(self, before_label, after_label, parent=None):
        super().__init__(parent)
        self.frames = [QImage(), QImage()]
        self.labels = [before_label, after_label]
        self.mode = 'side'
        self.split = .5
        self.zoom = None
        self.center = QPointF(.5,.5)
        self._drag = None
        self.setMinimumSize(480,260)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_frame(self, index, image):
        if not image.isNull():
            self.frames[index] = image
            self.update()

    def fit(self):
        self.zoom = None
        self.center = QPointF(.5,.5)
        self.update()

    def actual_pixels(self):
        self.zoom = 1.0
        self.center = QPointF(.5,.5)
        self.update()

    def master_size(self):
        frame = self.frames[1] if not self.frames[1].isNull() else self.frames[0]
        return QSizeF(frame.size()) if not frame.isNull() else QSizeF(1,1)

    def _view(self):
        if self.mode == 'split':return QRectF(self.rect())
        return QRectF(0,0,(self.width()-12)/2,self.height())

    def _scale(self, view):
        size = self.master_size()
        return self.zoom/self.devicePixelRatioF() if self.zoom is not None else min(view.width()/size.width(),view.height()/size.height())

    def _draw_frame(self, painter, index, view):
        image = self.frames[index]
        if image.isNull():return
        size = self.master_size()
        scale = self._scale(view)
        origin = view.center() - QPointF(self.center.x()*size.width()*scale,self.center.y()*size.height()*scale)
        canvas = QRectF(origin,QSizeF(size.width()*scale,size.height()*scale))
        fit = QSizeF(image.size()).scaled(canvas.size(),Qt.AspectRatioMode.KeepAspectRatio)
        rectangle = QRectF(canvas.center()-QPointF(fit.width()/2,fit.height()/2),fit)
        painter.drawImage(rectangle,image)

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.fillRect(self.rect(),QColor('#101216'))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        if self.mode == 'side':
            first = self._view()
            second = QRectF(first.right()+12,0,first.width(),first.height())
            for index,view in enumerate((first,second)):
                painter.save();painter.setClipRect(view)
                self._draw_frame(painter,index,view)
                painter.restore()
                painter.setPen(QColor('#ded9ff'))
                painter.drawText(view.adjusted(12,10,-12,-10),Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft,self.labels[index])
        else:
            view = self._view()
            self._draw_frame(painter,0,view)
            x = self.width()*self.split
            painter.save();painter.setClipRect(QRectF(x,0,self.width()-x,self.height()))
            self._draw_frame(painter,1,view)
            painter.restore()
            painter.setPen(QColor('#b4a7ff'))
            painter.drawLine(QPointF(x,0),QPointF(x,self.height()))
            painter.drawText(12,24,self.labels[0])
            painter.drawText(int(x)+12,24,self.labels[1])

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self._drag = (event.position(),QPointF(self.center))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self,event):
        if self._drag:
            delta = event.position()-self._drag[0]
            size = self.master_size();scale=self._scale(self._view())
            self.center = QPointF(max(0,min(1,self._drag[1].x()-delta.x()/(size.width()*scale))),
                                  max(0,min(1,self._drag[1].y()-delta.y()/(size.height()*scale))))
            self.update()

    def mouseReleaseEvent(self,event):
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self,event):
        self.zoom = max(.05,min(8,self._scale(self._view())*self.devicePixelRatioF()*(1.2 if event.angleDelta().y()>0 else 1/1.2)))
        self.update()
        event.accept()


class PlaybackDialog(QDialog):
    def __init__(self,before,after,audio_source,audio_offset,duration,fps,translate,
                 before_label,after_label,parent=None,initial_frames=None,initial_position=0):
        super().__init__(parent)
        self.tr = translate
        self.offset = round(audio_offset*1000)
        self.duration_ms = round(duration*1000)
        self.fps = fps or 24
        self._closing = False
        self._seeking = False
        self._initial_position = max(0,min(self.duration_ms,initial_position))
        self._loaded_positions = [False,False]
        self._audio_initialized = False
        self.setWindowTitle(self.tr('Playback comparison'))
        self.resize(1440,900)
        layout = QVBoxLayout(self)
        note = QLabel(self.tr('Playback is synchronized approximately. Sound comes from the original video at the sample time.'))
        note.setWordWrap(True);layout.addWidget(note)
        tools = QHBoxLayout()
        self.play_button = QPushButton(self.tr('Play'))
        self.play_button.clicked.connect(self.toggle_play)
        tools.addWidget(self.play_button)
        for label,callback in [('Previous frame',lambda:self.step(-1)),('Next frame',lambda:self.step(1))]:
            button=QPushButton(self.tr(label));button.clicked.connect(callback);tools.addWidget(button)
        self.view_mode = QComboBox()
        self.view_mode.addItem(self.tr('Side by side'),'side')
        self.view_mode.addItem(self.tr('Split view'),'split')
        self.view_mode.currentIndexChanged.connect(self.change_view)
        tools.addWidget(self.view_mode)
        self.canvas = ComparisonCanvas(before_label,after_label,self)
        if initial_frames:
            for index,image in enumerate(initial_frames):self.canvas.set_frame(index,image)
        self.canvas.setToolTip(self.tr('Drag to pan · wheel to zoom. Reference is fitted to the output canvas; 100% uses output pixels.'))
        for label,callback in [('Fit',self.canvas.fit),('100%',self.canvas.actual_pixels)]:
            button=QPushButton(self.tr(label));button.clicked.connect(callback);tools.addWidget(button)
        self.sound = QCheckBox(self.tr('Sound from original'))
        self.sound.setChecked(True)
        tools.addWidget(self.sound)
        tools.addStretch();layout.addLayout(tools)
        layout.addWidget(self.canvas,1)
        self.split_slider = QSlider(Qt.Orientation.Horizontal)
        self.split_slider.setRange(0,100);self.split_slider.setValue(50)
        self.split_slider.setAccessibleName(self.tr('Split position'))
        self.split_slider.valueChanged.connect(self.change_split)
        self.split_slider.hide();layout.addWidget(self.split_slider)
        timeline = QHBoxLayout()
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0,self.duration_ms)
        self.timeline.setAccessibleName(self.tr('Sample time'))
        self.timeline.sliderPressed.connect(lambda:setattr(self,'_seeking',True))
        self.timeline.sliderReleased.connect(self._release_seek)
        self.timeline.sliderMoved.connect(self.seek)
        timeline.addWidget(self.timeline,1)
        self.clock = QLabel();timeline.addWidget(self.clock)
        layout.addLayout(timeline)
        footer = QHBoxLayout()
        self.status = QLabel(self.tr('Drag to pan · wheel to zoom. Reference is fitted to the output canvas; 100% uses output pixels.'))
        self.status.setWordWrap(True);footer.addWidget(self.status,1)
        close=QPushButton(self.tr('Close'));close.clicked.connect(self.accept);footer.addWidget(close)
        layout.addLayout(footer)
        self.players = []
        self.sinks = []
        for index,path in enumerate((before,after)):
            player=QMediaPlayer(self)
            sink=QVideoSink(self)
            sink.videoFrameChanged.connect(lambda frame,i=index:self.canvas.set_frame(i,frame.toImage()))
            player.setVideoSink(sink)
            player.errorOccurred.connect(lambda error,message:self.status.setText(self.tr('Playback error')+': '+message))
            player.mediaStatusChanged.connect(lambda status,i=index:self._video_loaded(i,status))
            self.players.append(player);self.sinks.append(sink)
            player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.audio = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(.5)
        self.audio.setAudioOutput(self.audio_output)
        self.audio.hasAudioChanged.connect(self.sound.setEnabled)
        self.audio.errorOccurred.connect(self._audio_error)
        self.audio.mediaStatusChanged.connect(self._audio_loaded)
        self.audio.setSource(QUrl.fromLocalFile(str(Path(audio_source).resolve())))
        self.sound.toggled.connect(lambda enabled:self.audio_output.setMuted(not enabled))
        self.players[1].positionChanged.connect(self._position)
        self.players[1].mediaStatusChanged.connect(self._media_status)
        self.timer=QTimer(self)
        self.timer.setInterval(100);self.timer.timeout.connect(self._synchronize);self.timer.start()
        self._position(self._initial_position)

    def _video_loaded(self,index,status):
        if not self._loaded_positions[index] and status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia):
            self._loaded_positions[index]=True
            self.players[index].setPosition(self._initial_position)

    def _audio_loaded(self,status):
        if not self._closing and not self._audio_initialized and status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia):
            self._audio_initialized = True
            self.audio.setActiveVideoTrack(-1)
            self.sound.setEnabled(self.audio.hasAudio())
            self.audio.setPosition(self.offset+self.players[1].position())

    def _audio_error(self,error,message):
        self.sound.setChecked(False);self.sound.setEnabled(False)
        self.status.setText(self.tr('Audio unavailable')+': '+message)

    def change_view(self):
        self.canvas.mode=self.view_mode.currentData()
        self.split_slider.setVisible(self.canvas.mode=='split')
        self.canvas.update()

    def change_split(self,value):
        self.canvas.split=value/100;self.canvas.update()

    def _position(self,value):
        if not self._seeking:
            self.timeline.setValue(min(value,self.duration_ms))
        def stamp(ms):
            seconds=max(0,ms)//1000
            return f'{seconds//60:02d}:{seconds%60:02d}.{max(0,ms)%1000:03d}'
        self.clock.setText(stamp(value)+' / '+stamp(self.duration_ms) + ' · ' + self.tr('Frame') + f' {round(value * self.fps / 1000)}')

    def _media_status(self,status):
        if status==QMediaPlayer.MediaStatus.EndOfMedia:
            self.pause()

    def toggle_play(self):
        if self.players[1].playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.pause();return
        if self.players[1].position()>=self.duration_ms-50:self.seek(0)
        for player in self.players:player.play()
        self.audio.setPosition(self.offset+self.players[1].position());self.audio.play()
        self.play_button.setText(self.tr('Pause'))

    def pause(self):
        for player in self.players+[self.audio]:player.pause()
        self.play_button.setText(self.tr('Play'))

    def seek(self,value):
        value=max(0,min(self.duration_ms,value))
        for player in self.players:player.setPosition(value)
        self.audio.setPosition(self.offset+value)
        self._position(value)

    def _release_seek(self):
        self._seeking=False;self.seek(self.timeline.value())

    def step(self,direction):
        self.pause()
        frame = max(0, round(self.players[1].position() * self.fps / 1000) + direction)
        self.seek(round(frame * 1000 / self.fps))

    def _synchronize(self):
        if self._closing:return
        position=self.players[1].position()
        if position>=self.duration_ms:
            self.pause();return
        if self.players[1].playbackState()!=QMediaPlayer.PlaybackState.PlayingState:return
        if abs(self.players[0].position()-position)>max(25, round(1000/self.fps)):
            self.players[0].setPosition(position)
        if self.sound.isEnabled() and abs(self.audio.position()-self.offset-position)>120:
            self.audio.setPosition(self.offset+position)

    def _shutdown(self):
        if self._closing:return
        self._closing=True;self.timer.stop()
        for player in self.players+[self.audio]:
            player.stop();player.setSource(QUrl())

    def done(self,result):
        self._shutdown();super().done(result)

    def closeEvent(self,event):
        self._shutdown();super().closeEvent(event)
