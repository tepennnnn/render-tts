package com.example.voicelab.player

import android.content.Context
import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import java.io.File

/**
 * A simple wrapper around ExoPlayer to handle audio playback.
 */
class AudioPlayer(context: Context) {

    private val exoPlayer = ExoPlayer.Builder(context).build()

    /**
     * Prepares and plays the audio from a file.
     */
    fun playFile(file: File) {
        val uri = Uri.fromFile(file)
        val mediaItem = MediaItem.fromUri(uri)
        exoPlayer.setMediaItem(mediaItem)
        exoPlayer.prepare()
        exoPlayer.play()
    }

    fun play() {
        if (exoPlayer.playbackState == Player.STATE_ENDED) {
            exoPlayer.seekTo(0)
        }
        exoPlayer.play()
    }

    fun pause() {
        exoPlayer.pause()
    }

    fun stop() {
        exoPlayer.stop()
    }

    fun release() {
        exoPlayer.release()
    }

    /**
     * Provides access to the player state for UI updates.
     */
    fun getPlayer(): Player = exoPlayer
}
