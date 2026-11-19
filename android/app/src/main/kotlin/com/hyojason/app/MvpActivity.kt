package com.hyojason.app

import android.content.Intent
import android.os.Bundle
import android.widget.LinearLayout
import com.hyojason.app.child.HomeActivity

class MvpActivity : HomeActivity() {
    private lateinit var assistance: Assistance

    override fun onCreate(savedInstanceState: Bundle?) {
        assistance = Assistance(this)
        super.onCreate(savedInstanceState)
    }

    override fun drawAssistanceControls(root: LinearLayout, paired: Boolean, inCall: Boolean) {
        assistance.draw(root, paired, inCall)
    }

    override fun takeHelpCompletionNotice(): Boolean = assistance.takeCompletionNotice()

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == Assistance.CAPTURE_REQUEST) assistance.onApproval(resultCode, data)
    }

    override fun onDestroy() {
        assistance.close()
        super.onDestroy()
    }
}
