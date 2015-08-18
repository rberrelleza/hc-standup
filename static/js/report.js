(function() {

  function initWebSocket(baseUrl) {
    var uri = new URI(baseUrl);
    var socket = new WebSocket((uri.protocol() === "https" ? "wss://" : "ws://") +
            uri.hostname() + "/websocket?signed_request=" + signedRequest);

    socket.onmessage = function(event) {
      var message = JSON.parse(event.data);
      var existingStatus = $("[data-user-id=" + message["user_id"] + "]");
      if (existingStatus.length > 0) {
        if (message.html !== "") {
          existingStatus.replaceWith(message.html);
        } else {
          existingStatus.remove();
        }
      } else {
        $(".statuses").append(message.html);
      }

    };
  }

  $(document).ready(function() {

    var baseUrl = $("meta[name=base-url]").attr("content");

    var $spinner = $(".spinner-container");
    $spinner.spin("medium");

    initWebSocket(baseUrl);

    $.ajax({
      url: baseUrl + "/status_view",
      type: "GET",
      dataType: "html"
    }).done(function(html) {
      $spinner.data().spinner.stop();
      $(".statuses").append(html);
    });

    $(".create-new-button").click(function(e) {

      AP.require('dialog', function(dialog) {
        var integration = {
          addon_key: "hc-standup",
          full_key: "hc-standup:hcstandup.dialog",
          key: "hcstandup.dialog",
          type: "webPanel",
          name: "Create new report",
          options: {},
          url: baseUrl + "/dialog"
        };

        var roomId = $(e.target).data("room-id");
        dialog.open({
          integration: integration,
          room_id: roomId
        })
      });
    });
  });



})();