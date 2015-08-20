(function() {

  $(document).ready(function() {

    var baseUrl = $("meta[name=base-url]").attr("content");

    $("#d-save-btn1").click(function(e) {

      $.ajax({
        url: baseUrl + "/create",
        type: "POST",
        contentType: "application/json; charset=UTF-8",
        data: JSON.stringify({
          message: $(".create-dialog textarea").val()
        })
      }).done(function() {
        AP.require('dialog', function(dialog) {
          dialog.close({
            key: "hcstandup.dialog"
          });
        })
      });

      e.preventDefault();
    });

    // Not used yet, waiting for a fix
    AP.register({
      'dialog-button-click': function (event, cb) {
        $.ajax({
          url: baseUrl + "/create",
          type: "POST",
          contentType: "application/json; charset=UTF-8",
          data: JSON.stringify({
            message: $(".create-dialog textarea").val()
          })
        }).done(function() {
          AP.dispatch('hc-standup-new-report-submitted', {}, {});
          cb(true);
        });
      }
    });

  });



})();